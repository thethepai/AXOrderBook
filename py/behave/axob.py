    # -*- coding: utf-8 -*-

'''
简单的行为模型，目标：
  * 只针对单只股票
  * 支持撮合和非撮合
  * 支持深圳、上海
  * 支持有涨跌停价、无涨跌停价
  * 支持创业板价格笼子
  * 支持股票和etf

  * 尽量倾向便于FPGA硬件实现，梳理流程，不考虑面向C/C++实现
  * 将遍历全市场以验证正确性，为提升验证效率，可能用C重写一遍
  * 主要解决几个课题：
    * 撮合是否必须保存每个价格档位的链表？
    * 出快照的时机，是否必须等10ms超时？
    * 位宽检查
    * 访问次数
    * save/load
'''
from enum import Enum
from tool.msg_util import axsbe_base, axsbe_exe, axsbe_order, axsbe_snap_stock, price_level, CYB_cage_upper, CYB_cage_lower, bitSizeOf, MARKET_SUBTYPE, market_subtype
import tool.msg_util as msg_util
from tool.axsbe_base import SecurityIDSource_SSE, SecurityIDSource_SZSE, INSTRUMENT_TYPE, MsgType_exe_sse_bond
from copy import deepcopy

import logging
axob_logger = logging.getLogger(__name__)

#### 静态工作开关 ####
EXPORT_LEVEL_ACCESS = False # 是否导出对价格档位的读写请求

#### 内部计算精度 ####
APPSEQ_BIT_SIZE = 32    # 序列号，34b，约40亿，因为不同channel的序列号各自独立，所以单channel整形就够
PRICE_BIT_SIZE  = 25    # 价格，20b，33554431，股票:335544.31;基金:33554.431。（创业板上市首日有委托价格为￥188888.00的，若忽略这种特殊情况，则20b(10485.75)足够了）
QTY_BIT_SIZE    = 30    # 数量，30b，(1,073,741,823)，深圳2位小数，上海3位小数
LEVEL_QTY_BIT_SIZE    = QTY_BIT_SIZE+8    # 价格档位上的数量位宽 20220729:001258 qty=137439040000(38b)
TIMESTAMP_BIT_SIZE = 28 # 时戳精度 时-分-秒-10ms 最大15000000=24b; 上交所精度1ms，最大28b

PRICE_INTER_STOCK_PRECISION = 100  # 股票价格精度：2位小数，(深圳原始数据4位，上海3位)
PRICE_INTER_FUND_PRECISION  = 1000 # 基金价格精度：3位小数，(深圳原始数据4位，上海3位)
PRICE_INTER_KZZ_PRECISION  = 1000 # 可转债格精度：3位小数，(深圳原始数据4位，上海3位)

QTY_INTER_SZSE_PRECISION   = 100   # 数量精度：深圳2位小数
QTY_INTER_SSE_PRECISION    = 1000  # 数量精度：上海3位小数

SZSE_TICK_CUT = 1000000000 # 深交所时戳，日期以下精度
SZSE_TICK_MS_TAIL = 10 # 深交所时戳，尾部毫秒精度，以10ms为单位

PRICE_MAXIMUM = (1<<PRICE_BIT_SIZE)-1

CYB_ORDER_ENVALUE_MAX_RATE = 9

class SIDE(Enum): # 2bit
    BID = 0
    ASK = 1

    UNKNOWN = -1    # 仅用于测试

    def __str__(self):
        if self.value==0:
            return 'BID'
        elif self.value==1:
            return 'ASK'
        else:
            return 'UNKNOWN'


class TYPE(Enum): # 2bit
    LIMIT  = 0   #限价
    MARKET = 1   #市价
    SIDE   = 2   #本方最优

    UNKNOWN = -1    # 仅用于测试

# 用于将原始精度转换到ob精度
SZSE_STOCK_PRICE_RD = msg_util.PRICE_SZSE_INCR_PRECISION // PRICE_INTER_STOCK_PRECISION
SZSE_FUND_PRICE_RD = msg_util.PRICE_SZSE_INCR_PRECISION // PRICE_INTER_FUND_PRECISION
SZSE_KZZ_PRICE_RD = msg_util.PRICE_SZSE_INCR_PRECISION // PRICE_INTER_KZZ_PRECISION
SSE_STOCK_PRICE_RD = msg_util.PRICE_SSE_PRECISION // PRICE_INTER_STOCK_PRECISION
# SSE_FUND_PRICE_RD = msg_util.PRICE_SSE_PRECISION // PRICE_INTER_FUND_PRECISION TODO:确认精度 [low priority]

class ob_order():
    '''专注于内部使用的字段格式与位宽'''
    __slots__ = [
        'applSeqNum',
        'price',
        'qty',
        'side',
        'type',

        # for test olny
        'traded',
        'TransactTime',
    ]

    def __init__(self, order:axsbe_order, instrument_type:INSTRUMENT_TYPE):
        # self.securityID = order.SecurityID
        self.applSeqNum = order.ApplSeqNum

        if order.Side_str=='买入':
            self.side = SIDE.BID
        elif order.Side_str=='卖出':
            self.side = SIDE.ASK
        else:
            '''TODO-SSE'''
            self.side = SIDE.UNKNOWN

        if order.Type_str=='限价':          # SZ
            self.type = TYPE.LIMIT
        elif order.Type_str=='市价':        # SZ
            self.type = TYPE.MARKET
        elif order.Type_str=='本方最优':    # SZ
            self.type = TYPE.SIDE
        elif order.Type_str=='新增':        # SH
            self.type = TYPE.LIMIT
        else:
            self.type = TYPE.UNKNOWN

        if order.Price==msg_util.ORDER_PRICE_OVERFLOW: #原始价格越界 (不用管是否是LIMIT)
            self.price = PRICE_MAXIMUM  #本地也按越界处理，本地越界最终只影响到卖出加权价的计算
            axob_logger.warn(f'{order.SecurityID:06d} order ApplSeqNum={order.ApplSeqNum} Price over the maximum!')
            assert not(self.side==SIDE.BID and self.type==TYPE.LIMIT), f'{order.SecurityID:06d} BID order price overflow' #限价买单不应溢出
        else:
            if order.SecurityIDSource==SecurityIDSource_SZSE:
                if instrument_type==INSTRUMENT_TYPE.STOCK:
                    self.price = order.Price // SZSE_STOCK_PRICE_RD # 深圳 N13(4)，实际股票精度为分
                elif instrument_type==INSTRUMENT_TYPE.FUND:
                    self.price = order.Price // SZSE_FUND_PRICE_RD # 深圳 N13(4)，实际基金精度为厘
                elif instrument_type==INSTRUMENT_TYPE.KZZ:
                    self.price = order.Price // SZSE_KZZ_PRICE_RD # 深圳 N13(4)，实际基金精度为厘
                else:
                    axob_logger.error(f'order SZSE ApplSeqNum={order.ApplSeqNum} instrument_type={instrument_type} not support!')
            elif order.SecurityIDSource==SecurityIDSource_SSE:
                if instrument_type==INSTRUMENT_TYPE.STOCK:
                    self.price = order.Price // SSE_STOCK_PRICE_RD # 上海 原始数据3位小数
                elif instrument_type==INSTRUMENT_TYPE.BOND:
                    self.price = order.Price                       # 上海 原始数据3位小数，债券需要3位小数
                else:
                    axob_logger.error(f'order SSE ApplSeqNum={order.ApplSeqNum} instrument_type={instrument_type} not support!')
            else:
                self.price = 0
        self.traded = False #仅用于测试：市价单，当有成交后，市价单的价格将确定
        self.TransactTime = order.TransactTime #仅用于测试：市价单，当有后续消息来而导致插入订单簿时，生成的订单簿用此时戳

        self.qty = order.OrderQty    # 深圳2位小数;上海3位小数

        ## 位宽及精度舍入可行性检查
        if self.applSeqNum >= (1<<APPSEQ_BIT_SIZE) and self.applSeqNum!=0xffffffffffffffff:
            axob_logger.error(f'{order.SecurityID:06d} order ApplSeqNum={order.ApplSeqNum} ovf!')

        if self.price >= (1<<PRICE_BIT_SIZE):
            self.price = (1<<PRICE_BIT_SIZE)-1
            axob_logger.error(f'{order.SecurityID:06d} order ApplSeqNum={order.ApplSeqNum} Price={order.Price} ovf!')  # 无涨跌停价时可能，即使限价单也可能溢出，且会被前端处理成0x7fff_ffff

        if self.qty >= (1<<QTY_BIT_SIZE):
            axob_logger.error(f'{order.SecurityID:06d} order ApplSeqNum={order.ApplSeqNum} Volumn={order.OrderQty} ovf!')

        if self.type==TYPE.LIMIT and order.Price!=msg_util.ORDER_PRICE_OVERFLOW:   #检查限价单价格是否溢出；市价单价格是无效值，不可参与检查
            if order.SecurityIDSource==SecurityIDSource_SZSE:
                if instrument_type==INSTRUMENT_TYPE.STOCK and order.Price % SZSE_STOCK_PRICE_RD:
                    axob_logger.error(f'{order.SecurityID:06d} order SZSE STOCK ApplSeqNum={order.ApplSeqNum} Price={order.Price} precision dnf!')  #当被前端处理成0x7fff_ffff时 会有余数
                elif instrument_type==INSTRUMENT_TYPE.FUND and order.Price % SZSE_FUND_PRICE_RD:
                    axob_logger.error(f'{order.SecurityID:06d} order SZSE FUND ApplSeqNum={order.ApplSeqNum} Price={order.Price} precision dnf!')  #当被前端处理成0x7fff_ffff时 会有余数
                elif instrument_type==INSTRUMENT_TYPE.KZZ and order.Price % SZSE_KZZ_PRICE_RD:
                    axob_logger.error(f'{order.SecurityID:06d} order SZSE KZZ ApplSeqNum={order.ApplSeqNum} Price={order.Price} precision dnf!')  #当被前端处理成0x7fff_ffff时 会有余数
            elif order.SecurityIDSource==SecurityIDSource_SSE:
                if instrument_type==INSTRUMENT_TYPE.STOCK and order.Price % SSE_STOCK_PRICE_RD:
                    axob_logger.error(f'{order.SecurityID:06d} order SSE STOCK ApplSeqNum={order.ApplSeqNum} Price={order.Price} precision dnf!')

    def save(self):
        '''save/load 用于保存/加载测试时刻'''
        data = {}
        for attr in self.__slots__:
            value = getattr(self, attr)
            data[attr] = value
        return data

    def load(self, data):
        for attr in self.__slots__:
            setattr(self, attr, data[attr])


class ob_exec():
    '''专注于内部使用的字段格式与位宽'''
    __slots__ = [
        'LastPx',
        'LastQty',
        'BidApplSeqNum',
        'OfferApplSeqNum',
        'TradingPhaseMarket',

        # for test olny
        'TransactTime',
    ]

    def __init__(self, exec:axsbe_exe, instrument_type:INSTRUMENT_TYPE):
        self.BidApplSeqNum = exec.BidApplSeqNum
        self.OfferApplSeqNum = exec.OfferApplSeqNum
        self.TradingPhaseMarket = exec.TradingPhaseMarket

        if exec.SecurityIDSource==SecurityIDSource_SZSE:
            if instrument_type==INSTRUMENT_TYPE.STOCK:
                self.LastPx = exec.LastPx // SZSE_STOCK_PRICE_RD # 深圳 N13(4)，实际股票精度为分
            elif instrument_type==INSTRUMENT_TYPE.FUND:
                self.LastPx = exec.LastPx // SZSE_FUND_PRICE_RD # 深圳 N13(4)，实际基金精度为厘
            elif instrument_type==INSTRUMENT_TYPE.KZZ:
                self.LastPx = exec.LastPx // SZSE_KZZ_PRICE_RD # 深圳 N13(4)，实际可转债精度为厘
            else:
                axob_logger.error(f'exec SZSE ApplSeqNum={exec.ApplSeqNum} instrument_type={instrument_type} not support!')
        elif exec.SecurityIDSource==SecurityIDSource_SSE:
            if instrument_type==INSTRUMENT_TYPE.STOCK:
                self.LastPx = exec.LastPx // SSE_STOCK_PRICE_RD # 上海 原始数据3位小数
            else:
                axob_logger.error(f'order SSE ApplSeqNum={exec.ApplSeqNum} instrument_type={instrument_type} not support!')
        else:
            self.LastPx = 0

        self.LastQty = exec.LastQty    # 深圳2位小数;上海3位小数

        self.TransactTime = exec.TransactTime

        ## 位宽及精度舍入可行性检查
        # 不去检查SeqNum位宽了，SeqNum总能在order list中找到，因此肯定已经检查过了。
        # price/qty同理
        # if self.LastPx >= (1<<PRICE_BIT_SIZE):
        #     axob_logger.error(f'{exec.SecurityID:06d} order ApplSeqNum={exec.ApplSeqNum} LastPx={exec.LastPx} ovf!')  # 无涨跌停价时可能，即使限价单也可能溢出，且会被前端处理成0x7fff_ffff

        # if self.LastQty >= (1<<QTY_BIT_SIZE):
        #     axob_logger.error(f'{exec.SecurityID:06d} order ApplSeqNum={exec.ApplSeqNum} LastQty={exec.LastQty} ovf!')



class ob_cancel():
    '''专注于内部使用的字段格式与位宽'''
    __slots__ = [
        'applSeqNum',
        'qty',
        'price',
        'side',

        # for test olny
        'TransactTime',
    ]
    def __init__(self, ApplSeqNum, Qty, Price, Side, TransactTime, SecurityIDSource, instrument_type, SecurityID):
        self.applSeqNum = ApplSeqNum    #
        self.qty = Qty
        if SecurityIDSource==SecurityIDSource_SZSE:
            self.price = 0  #深圳撤单不带价格
        elif SecurityIDSource==SecurityIDSource_SSE:
            if instrument_type==INSTRUMENT_TYPE.STOCK:
                self.price = Price // SSE_STOCK_PRICE_RD # 上海 原始数据3位小数
            else:
                axob_logger.error(f'{SecurityID:06d} cancel SSE ApplSeqNum={ApplSeqNum} instrument_type={instrument_type} not support!')
        else:
            axob_logger.error(f'{SecurityID:06d} cancel ApplSeqNum={ApplSeqNum} SecurityIDSource={SecurityIDSource} unknown!')
        self.side = Side

        self.TransactTime = TransactTime

        if self.applSeqNum >= (1<<APPSEQ_BIT_SIZE):
            axob_logger.error(f'{SecurityID:06d} cancel ApplSeqNum={ApplSeqNum} ovf!')

        if self.price >= (1<<PRICE_BIT_SIZE):
            axob_logger.error(f'{SecurityID:06d} cancel ApplSeqNum={ApplSeqNum} Price={Price} ovf!')

        if self.qty >= (1<<QTY_BIT_SIZE):
            axob_logger.error(f'{SecurityID:06d} cancel ApplSeqNum={ApplSeqNum} Volumn={Qty} ovf!')



class level_node():
    __slots__ = [
        'price',
        'qty',

        # for test olny
        # 'ts',
    ]
    def __init__(self, price, qty, ts):
        self.price = price
        self.qty = qty

        # 目前没用，仅供调试
        # self.ts = [ts] 目前已经无法维护序列号了，因为没有去检查成交是部分成交还是全部成交

    def save(self):
        '''save/load 用于保存/加载测试时刻'''
        data = {}
        for attr in self.__slots__:
            value = getattr(self, attr)
            if attr=='ts':
                data[attr] = deepcopy(value)
            else:
                data[attr] = value
        return data

    def load(self, data):
        for attr in self.__slots__:
            setattr(self, attr, data[attr])

    def __str__(self) -> str:
        return f'{self.price}\t{self.qty}'

class AX_SIGNAL(Enum):  # 发送给AXOB的信号
    OPENCALL_BGN  = 0  # 开盘集合竞价开始
    OPENCALL_END  = 1  # 开盘集合竞价结束
    AMTRADING_BGN = 2  # 上午连续竞价开始
    AMTRADING_END = 3  # 上午连续竞价结束
    PMTRADING_BGN = 4  # 下午连续竞价开始
    PMTRADING_END = 5  # 下午连续竞价结束
    ALL_END = 6        # 闭市

CHANNELNO_INIT = -1

class AXOB():
    __slots__ = [
        'SecurityID',
        'SecurityIDSource',
        'instrument_type',

        'order_map',    # map of ob_order
        'illegal_order_map',    # map of illegal_order
        'bid_level_tree', # map of level_node
        'ask_level_tree', # map of level_node

        'NumTrades',
        'bid_max_level_price',
        'bid_max_level_qty',
        'ask_min_level_price',
        'ask_min_level_qty',
        'LastPx',
        'HighPx',
        'LowPx',
        'OpenPx',

        'closePx_ready',

        'constantValue_ready',
        'ChannelNo',
        'PrevClosePx',
        'DnLimitPx',
        'UpLimitPx',
        'DnLimitPrice',
        'UpLimitPrice',
        'YYMMDD',
        'current_inc_tick',
        'BidWeightSize',
        'BidWeightValue',
        'AskWeightSize',
        'AskWeightValue',
        'AskWeightSizeEx',
        'AskWeightValueEx',

        'TotalVolumeTrade',
        'TotalValueTrade',

        'holding_order',
        'holding_nb',

        'TradingPhaseMarket',
        # 'VolatilityBreaking_end_tick',

        'AskWeightPx_uncertain',

        'market_subtype',
        'bid_cage_upper_ex_min_level_price',
        'bid_cage_upper_ex_min_level_qty',
        'ask_cage_lower_ex_max_level_price',
        'ask_cage_lower_ex_max_level_qty',
        'bid_cage_ref_px',
        'ask_cage_ref_px',
        'bid_waiting_for_cage',
        'ask_waiting_for_cage',

        # profile
        'pf_order_map_maxSize',
        'pf_level_tree_maxSize',
        'pf_bid_level_tree_maxSize',
        'pf_ask_level_tree_maxSize',
        'pf_AskWeightSize_max',
        'pf_AskWeightValue_max',
        'pf_BidWeightSize_max',
        'pf_BidWeightValue_max',

        # for test olny
        'msg_nb',
        'rebuilt_snaps',    # list of snap
        'market_snaps',     # list of snap
        'last_snap',
        'last_inc_applSeqNum',

        'logger',
        'DBG',
        'INFO',
        'WARN',
        'ERR',
    ]
    def __init__(self, SecurityID:int, SecurityIDSource, instrument_type:INSTRUMENT_TYPE, load_data=None):
        '''
        TODO: holding_order的处理是否统一到一处？必须要实现！
        TODO: 增加时戳输入，用于结算各自缓存，如市价单
        '''
        if load_data:
            self.load(load_data)
        else:
            self.SecurityID = SecurityID
            self.SecurityIDSource = SecurityIDSource #"证券代码源101=上交所;102=深交所;103=香港交易所" 在hls中用宏或作为模板参数设置
            self.instrument_type = instrument_type

            ## 结构数据：
            self.order_map = {} #订单队列，以applSeqNum作为索引
            self.illegal_order_map = {} #
            self.bid_level_tree = {} #买方价格档，以价格作为索引
            self.ask_level_tree = {} #卖方价格档

            self.NumTrades = 0
            self.bid_max_level_price = 0
            self.bid_max_level_qty = 0
            self.ask_min_level_price = 0
            self.ask_min_level_qty = 0
            self.LastPx = 0
            self.HighPx = 0
            self.LowPx = 0
            self.OpenPx = 0

            self.closePx_ready = False

            self.constantValue_ready = False
            self.ChannelNo = CHANNELNO_INIT #来自于快照
            self.PrevClosePx = 0 #来自于快照 深圳要处理到内部精度，用于在还原快照时比较
            self.DnLimitPx = 0  # #来自于快照 无涨跌停价时为0x7fffffff
            self.UpLimitPx = 0  # #来自于快照 无涨跌停价时为100
            self.DnLimitPrice = 0  
            self.UpLimitPrice = 0  
            self.YYMMDD = 0     #来自于快照
            self.current_inc_tick = 0 #来自于逐笔 时-分-秒-10ms
            
            self.BidWeightSize = 0
            self.BidWeightValue = 0
            self.AskWeightSize = 0
            self.AskWeightValue = 0
            self.AskWeightSizeEx = 0
            self.AskWeightValueEx = 0

            self.TotalVolumeTrade = 0
            self.TotalValueTrade = 0

            self.holding_order = None
            self.holding_nb = 0

            self.TradingPhaseMarket = axsbe_base.TPM.Starting

            self.AskWeightPx_uncertain = False #卖出加权价格无法确定（由于卖出委托价格越界）

            self.market_subtype = market_subtype(SecurityIDSource, SecurityID)

            self.bid_cage_upper_ex_min_level_price = 0 #买方价格笼子上沿之外的最低价，超过买入基准价的102%
            self.bid_cage_upper_ex_min_level_qty = 0
            self.ask_cage_lower_ex_max_level_price = 0 #卖方价格笼子下沿之外的最高价，低于卖出基准价的98%
            self.ask_cage_lower_ex_max_level_qty = 0
            self.bid_cage_ref_px = 0 #买方价格笼子基准价格 卖方一档价格 -> 买方一档价格 -> 最近成交价 -> 前收盘价，小于等于基准价的102%的在笼子内，大于的在笼子外（被隐藏）
            self.ask_cage_ref_px = 0 #卖方价格笼子基准价格 买方一档价格 -> 卖方一档价格 -> 最近成交价 -> 前收盘价，大于等于基准价的98%的在笼子内，小于的在笼子外（被隐藏）
            self.bid_waiting_for_cage = False
            self.ask_waiting_for_cage = False

            ## 调试数据，仅用于测试算法是否正确：
            self.pf_order_map_maxSize = 0
            self.pf_level_tree_maxSize = 0
            self.pf_bid_level_tree_maxSize = 0
            self.pf_ask_level_tree_maxSize = 0
            self.pf_AskWeightSize_max = 0
            self.pf_AskWeightValue_max = 0
            self.pf_BidWeightSize_max = 0
            self.pf_BidWeightValue_max = 0


            self.msg_nb = 0
            self.rebuilt_snaps = {}
            self.market_snaps = {}
            self.last_snap = None
            self.last_inc_applSeqNum = 0

            ## 日志
            self.logger = logging.getLogger(f'{self.SecurityID:06d}')
            g_logger = logging.getLogger('main')
            self.logger.setLevel(g_logger.getEffectiveLevel())
            axob_logger.setLevel(g_logger.getEffectiveLevel())
            for h in g_logger.handlers:
                self.logger.addHandler(h)
                axob_logger.addHandler(h) #这里补上模块日志的handler，有点ugly TODO: better way [low prioryty]

            self.DBG = self.logger.debug
            self.INFO = self.logger.info
            self.WARN = self.logger.warning
            self.ERR = self.logger.error

    def onMsg(self, msg):
        '''处理总入口'''
        if isinstance(msg, (axsbe_order, axsbe_exe, axsbe_snap_stock)):
            if msg.SecurityID!=self.SecurityID:
                return

            # 深交所：始终逐笔序列号递增，这里做检查
            # 上交所：非合并流逐笔会乱序，不检查
            if self.SecurityIDSource==SecurityIDSource_SZSE and isinstance(msg, (axsbe_order, axsbe_exe)) and msg.ApplSeqNum<=self.last_inc_applSeqNum:
                self.ERR(f"ApplSeqNum={msg.ApplSeqNum} <= last_inc_applSeqNum={self.last_inc_applSeqNum} repeated or outOfOrder!")
                return

            if isinstance(msg, (axsbe_order, axsbe_exe)):
                # if self.market_subtype==MARKET_SUBTYPE.SZSE_STK_GEM and self.TradingPhaseMarket==axsbe_base.TPM.PMTrading and msg.TradingPhaseMarket==axsbe_base.TPM.CloseCall:
                #     # 创业板进入收盘集合竞价，敞开价格笼子，将外面的隐藏订单放进来
                #     self.openCage()
                #     self.genSnap()

                assert self.constantValue_ready, f'{self.SecurityID:06d} constant values not ready!'

                self._useTimestamp(msg.TransactTime)

                if self.TradingPhaseMarket!=axsbe_base.TPM.VolatilityBreaking:
                    self.TradingPhaseMarket = msg.TradingPhaseMarket # 只用逐笔，在阶段切换期间，逐笔和快照的速率不同，可能快照切了逐笔没切，或反过来，
                                                                    # 由于我们重建完全基于逐笔，快照仅用来做检查，故阶段切换基于逐笔。
                                                                    # 几个例外情况：
                                                                    #   在开盘集合竞价结束时可能没有成交；在进入中午休市时，没有逐笔。
                                                                    # 此时由更高层触发SIGNAL。
                # else:
                #     if self.VolatilityBreaking_end_tick==0: #波动性中断期间，逐笔成交到来说明中断结束
                #         if isinstance(msg, axsbe_exe) and msg.ExecType_str=='成交':
                #             self.VolatilityBreaking_end_tick = self.current_inc_tick
                #     else:
                #         if not(isinstance(msg, axsbe_exe) and msg.ExecType_str=='成交'): #中断结束后，有非逐笔成交
                #             self.TradingPhaseMarket = msg.TradingPhaseMarket

                

            if isinstance(msg, axsbe_order):
                self.onOrder(msg)
            elif isinstance(msg, axsbe_exe):
                self.onExec(msg)
            elif isinstance(msg, axsbe_snap_stock):
                self.onSnap(msg)

            # 深交所：始终逐笔序列号递增，这里做记录
            # 上交所：非合并流逐笔会乱序，不记录
            if self.SecurityIDSource==SecurityIDSource_SZSE and isinstance(msg, (axsbe_order, axsbe_exe)):
                self.last_inc_applSeqNum = msg.ApplSeqNum
        
        elif isinstance(msg, AX_SIGNAL):
            if msg==AX_SIGNAL.OPENCALL_END:
                if self.bid_max_level_price<self.ask_min_level_price and self.TradingPhaseMarket==axsbe_base.TPM.OpenCall: #双方最优价无法成交，否则等成交
                    self.TradingPhaseMarket = axsbe_base.TPM.PreTradingBreaking #自行修改交易阶段，使生成的快照为交易快照
                    self.genSnap()
            elif msg==AX_SIGNAL.AMTRADING_BGN:
                if self.TradingPhaseMarket==axsbe_base.TPM.PreTradingBreaking:
                    self.TradingPhaseMarket = axsbe_base.TPM.AMTrading
                    self.AskWeightSize += self.AskWeightSizeEx
                    self.AskWeightValue += self.AskWeightValueEx
                    self.genSnap()
            elif msg==AX_SIGNAL.AMTRADING_END:
                if self.TradingPhaseMarket==axsbe_base.TPM.AMTrading:
                    if self.holding_nb and self.holding_order.type==TYPE.MARKET:
                        self.insertOrder(self.holding_order)
                        self.holding_nb = 0
                    if self.holding_nb==0: #不再有缓存单
                        self.TradingPhaseMarket = axsbe_base.TPM.Breaking
                        self.genSnap()
            elif msg==AX_SIGNAL.PMTRADING_END:
                if self.TradingPhaseMarket==axsbe_base.TPM.PMTrading:
                    if self.holding_nb and self.holding_order.type==TYPE.MARKET:
                        self.insertOrder(self.holding_order)
                        self.holding_nb = 0
                    if self.holding_nb==0: #不再有缓存单
                        self.genSnap() # 先生成最后一个快照

                        self.TradingPhaseMarket = axsbe_base.TPM.CloseCall #自行修改交易阶段，使生成的快照为集合竞价快照
                        self.openCage() #开笼子，再生成集合竞价
                        self.genSnap()
            elif msg==AX_SIGNAL.ALL_END:
                # 收盘集合竞价结束，收盘价：
                #  沪市收盘价为当日该证券最后一笔交易前一分钟所有交易的成交量加权平均价（含最后一笔交易）。当日无成交的，以前收盘价为当日收盘价。
                #  深市的收盘价通过集合竞价的方式产生。收盘集合竞价不能产生收盘价的，以当日该证券最后一笔交易前一分钟所有交易的成交量加权平均价(含最后一笔交易)为收盘价。当日无成交的，以前收盘价为当日收盘价。 
                if self.SecurityIDSource==SecurityIDSource_SZSE:
                    if self.bid_max_level_price<self.ask_min_level_price and self.TradingPhaseMarket==axsbe_base.TPM.CloseCall: #双方最优价无法成交，否则等成交
                        self.TradingPhaseMarket = axsbe_base.TPM.Ending #自行修改交易阶段，使生成的快照为交易快照
                        self.closePx_ready = False  #等快照的价格作为最后价格
                    else:
                        self.closePx_ready = True   #直接生成快照
                        self.genSnap()
                else:
                    if self.bid_max_level_price<self.ask_min_level_price and self.TradingPhaseMarket==axsbe_base.TPM.CloseCall: #双方最优价无法成交，否则等成交
                        self.TradingPhaseMarket = axsbe_base.TPM.Ending #自行修改交易阶段，使生成的快照为交易快照
                    self.closePx_ready = False  #等快照的价格作为最后价格
        else:
            pass


        #if self.TradingPhaseMarket>=axsbe_base.TPM.Ending:
        # if self.msg_nb>=885:
        #    self._print_levels()


        ## 调试数据，仅用于测试算法是否正确：
        self.msg_nb += 1
        self.profile()

        if len(self.ask_level_tree):
            if self.market_subtype==MARKET_SUBTYPE.SZSE_STK_GEM and self.ask_cage_lower_ex_max_level_qty:
                assert self.ask_min_level_price>self.ask_cage_lower_ex_max_level_price, f'{self.SecurityID:06d} cache ask-min-price/cage-max NG'
            else:
                assert self.ask_min_level_price==min(self.ask_level_tree.keys()), f'{self.SecurityID:06d} cache ask-min-price NG'
                assert self.ask_min_level_qty==min(self.ask_level_tree.items(), key=lambda x: x[0])[1].qty, f'{self.SecurityID:06d} cache ask-min-qty NG'
        if len(self.bid_level_tree):
            if self.market_subtype==MARKET_SUBTYPE.SZSE_STK_GEM and self.bid_cage_upper_ex_min_level_qty:
                assert self.bid_max_level_price<self.bid_cage_upper_ex_min_level_price, f'{self.SecurityID:06d} cache bid-max-price/cage-min NG'
            else:
                assert self.bid_max_level_price==max(self.bid_level_tree.keys()), f'{self.SecurityID:06d} cache bid-max-price NG'
                assert self.bid_max_level_qty==max(self.bid_level_tree.items(), key=lambda x: x[0])[1].qty, f'{self.SecurityID:06d} ache bid-max-qty NG'

        if (self.TradingPhaseMarket==axsbe_base.TPM.AMTrading or self.TradingPhaseMarket==axsbe_base.TPM.PMTrading) and self.bid_max_level_qty and self.ask_min_level_qty:
            assert self.bid_max_level_price<self.ask_min_level_price, f'{self.SecurityID:06d} bid.max({self.bid_max_level_price})/ask.min({self.ask_min_level_price}) NG @{self.current_inc_tick}'

        static_AskWeightSize = 0
        static_AskWeightValue = 0
        for _,l in self.ask_level_tree.items():
            assert l.qty<(1<<LEVEL_QTY_BIT_SIZE), f'{self.SecurityID:06d} ask level qty={l.qty} ovf @{self.current_inc_tick}'
            if (self.ask_cage_lower_ex_max_level_qty==0 or l.price>self.ask_cage_lower_ex_max_level_price):
                static_AskWeightSize += l.qty
                static_AskWeightValue += l.price * l.qty
        if self.TradingPhaseMarket>=axsbe_base.TPM.AMTrading:
            assert static_AskWeightSize==self.AskWeightSize, f'{self.SecurityID:06d} static AskWeightSize={static_AskWeightSize}, dynamic AskWeightSize={self.AskWeightSize}'
            assert static_AskWeightValue==self.AskWeightValue, f'{self.SecurityID:06d} static AskWeightSize={static_AskWeightValue}, dynamic AskWeightValue={self.AskWeightValue}'
        else:
            assert static_AskWeightSize==self.AskWeightSize + self.AskWeightSizeEx, f'{self.SecurityID:06d} static AskWeightSize={static_AskWeightSize}, dynamic AskWeightSize={self.AskWeightSize}+{self.AskWeightSizeEx}'
            assert static_AskWeightValue==self.AskWeightValue + self.AskWeightValueEx, f'{self.SecurityID:06d} static AskWeightSize={static_AskWeightValue}, dynamic AskWeightValue={self.AskWeightValue}+{self.AskWeightValueEx}'

        static_BidWeightSize = 0
        static_BidWeightValue = 0
        for _,l in self.bid_level_tree.items():
            assert l.qty<(1<<LEVEL_QTY_BIT_SIZE), f'{self.SecurityID:06d} bid level qty={l.qty} ovf @{self.current_inc_tick}'
            if self.bid_cage_upper_ex_min_level_qty==0 or l.price<self.bid_cage_upper_ex_min_level_price:
                static_BidWeightSize += l.qty
                static_BidWeightValue += l.price * l.qty
        assert static_BidWeightSize==self.BidWeightSize, f'{self.SecurityID:06d} static BidWeightSize={static_BidWeightSize}, dynamic BidWeightSize={self.BidWeightSize}'
        assert static_BidWeightValue==self.BidWeightValue, f'{self.SecurityID:06d} static BidWeightValue={self.BidWeightValue}, dynamic BidWeightValue={self.BidWeightValue}'

        for _,ls in self.market_snaps.items():
            assert len(ls)!=0, f'{self.SecurityID:06d} market snap not pop clean'


    def openCage(self):
        self.DBG('openCage')
        # self._print_levels()

        ## 创业板上市头5日连续竞价、复牌集合竞价、收盘集合竞价的有效竞价范围是最近成交价的上下10%
        if self.UpLimitPx==msg_util.ORDER_PRICE_OVERFLOW: #无涨跌停限制=创业板上市头5日 TODO: 更精确
            ex_p = []
            self._export_level_access(f'LEVEL_ACCESS ASK inorder_list_inc //remove invalid price')
            for p, l in sorted(self.ask_level_tree.items(),key=lambda x:x[0], reverse=False):    #从小到大遍历
                if p>msg_util.CYB_match_upper(self.LastPx) or p<msg_util.CYB_match_lower(self.LastPx):
                    ex_p.append(p)
                    if not self.ask_cage_lower_ex_max_level_qty or p>self.ask_cage_lower_ex_max_level_price:    #属于被纳入动态统计的价格档
                        self.AskWeightSize -= l.qty
                        self.AskWeightValue -= p * l.qty
            for p in ex_p:
                self.ask_level_tree.pop(p)
                self._export_level_access(f'LEVEL_ACCESS ASK remove {p} //remove invalid price') #二叉树也不能边遍历边修改, TODO: 全部remove后再平衡？

            ex_p = []
            self._export_level_access(f'LEVEL_ACCESS BID inorder_list_dec //remove invalid price')
            for p, l in sorted(self.bid_level_tree.items(),key=lambda x:x[0], reverse=True):    #从大到小遍历
                if p>msg_util.CYB_match_upper(self.LastPx) or p<msg_util.CYB_match_lower(self.LastPx):
                    ex_p.append(p)
                    if not self.bid_cage_upper_ex_min_level_qty or p<self.bid_cage_upper_ex_min_level_price:    #属于被纳入动态统计的价格档
                        self.BidWeightSize -= l.qty
                        self.BidWeightValue -= p * l.qty
            for p in ex_p:
                self.bid_level_tree.pop(p)
                self._export_level_access(f'LEVEL_ACCESS BID remove {p} //remove invalid price') #二叉树也不能边遍历边修改, TODO: 全部remove后再平衡？


        if self.ask_cage_lower_ex_max_level_qty:
            self._export_level_access(f'LEVEL_ACCESS ASK inorder_list_inc while <={self.ask_cage_lower_ex_max_level_price} //openCage')
            for p, l in sorted(self.ask_level_tree.items(),key=lambda x:x[0], reverse=False):    #从小到大遍历
                if p<=self.ask_cage_lower_ex_max_level_price:
                    self.AskWeightSize += l.qty
                    self.AskWeightValue += p * l.qty
                else:
                    break

            self.ask_cage_lower_ex_max_level_qty = 0
            self.ask_min_level_price = min(self.ask_level_tree.keys())
            self.ask_min_level_qty = self.ask_level_tree[self.ask_min_level_price].qty
            self._export_level_access(f'LEVEL_ACCESS ASK locate_min //openCage') #TODO: 直接在上面遍历时赋值

        if self.bid_cage_upper_ex_min_level_qty:
            self._export_level_access(f'LEVEL_ACCESS BID inorder_list_dec while >={self.bid_cage_upper_ex_min_level_price} //openCage')
            for p, l in sorted(self.bid_level_tree.items(),key=lambda x:x[0], reverse=True):    #从大到小遍历
                if p>=self.bid_cage_upper_ex_min_level_price:
                    self.BidWeightSize += l.qty
                    self.BidWeightValue += p * l.qty
                else:
                    break

            self.bid_cage_upper_ex_min_level_qty = 0
            self.bid_max_level_price = max(self.bid_level_tree.keys())
            self.bid_max_level_qty = self.bid_level_tree[self.bid_max_level_price].qty
            self._export_level_access(f'LEVEL_ACCESS BID locate_max //openCage') #TODO: 直接在上面遍历时赋值
        # self._print_levels()


    def onOrder(self, order:axsbe_order):
        '''
        逐笔订单入口，统一提取市价单、限价单的关键字段到内部订单格式
        跳转到处理限价单或处理撤单
        '''
        self.DBG(f'msg#{self.msg_nb} onOrder:{order}')
        
        if self.holding_nb!=0: #把此前缓存的订单(市价/限价)插入LOB
            if self.holding_order.type == TYPE.MARKET and not self.holding_order.traded:
                self.ERR(f'市价单 {self.holding_order} 未伴随成交')
            self.insertOrder(self.holding_order)
            self.holding_nb = 0

            self._useTimestamp(self.holding_order.TransactTime)
            self.genSnap()   #先出一个snap，时戳用市价单的
            self._useTimestamp(order.TransactTime)

        if self.SecurityIDSource == SecurityIDSource_SZSE:
            _order = ob_order(order, self.instrument_type)
        elif self.SecurityIDSource == SecurityIDSource_SSE:
            # order or cancel
            if order.Type_str=='新增':
                _order = ob_order(order, self.instrument_type)
            elif order.Type_str=='删除':
                if order.Side_str=='买入':
                    Side=SIDE.BID
                elif order.Side_str=='卖出':
                    Side=SIDE.ASK
                _cancel = ob_cancel(order.OrderNo, order.Qty, order.Price, Side, order.TransactTime, self.SecurityIDSource, self.instrument_type, self.SecurityID)
                self.onCancel(_cancel)
                return
        else:
            return

        if _order.type==TYPE.MARKET:
            # 市价单，都必须在开盘之后
            if self.bid_max_level_qty==0 and self.ask_min_level_qty==0:
                raise '未定义模式:市价单早于价格档' #TODO: cover [Mid priority]

            # 市价单，几种可能：
            #    * 对手方最优价格申报：有成交、最后挂在对方一档或者二档，需要等时戳切换、新委托、新撤单到来的时候插入快照
            #    * 即时成交剩余撤销申报：最后有撤单
            #    * 全额成交或撤销申报：最后有撤单

        elif _order.type==TYPE.SIDE:
            # 本方最优，两种可能：
            #    * 本方最优价格申报 转限价单
            #    * 最优五档即时成交剩余撤销申报：最后有撤单，如果本方没有价格，立即撤单
            if _order.side==SIDE.BID:
                if self.bid_max_level_price!=0 and self.bid_max_level_qty!=0:   #本方有量
                    _order.price = self.bid_max_level_price
                else:
                    _order.price = self.DnLimitPrice
                    self.WARN(f'order #{_order.applSeqNum} 本方最优买单 但无本方价格!')
            else:
                if self.ask_min_level_price!=0 and self.ask_min_level_qty!=0:   #本方有量
                    _order.price = self.ask_min_level_price
                else:
                    _order.price = self.UpLimitPrice
                    self.WARN(f'order #{_order.applSeqNum} 本方最优卖单 但无本方价格!')
        else:
            pass
        self.onLimitOrder(_order)


    def onLimitOrder(self, order:ob_order):
        if self.TradingPhaseMarket==axsbe_base.TPM.OpenCall or self.TradingPhaseMarket==axsbe_base.TPM.CloseCall:
            #集合竞价期间，直接插入
            # if self.TradingPhaseMarket==axsbe_base.TPM.CloseCall and self.holding_nb!=0: #进入收盘集合竞价，但可能有市价单还在确认
            #     self.insertOrder(self.holding_order)
            #     self.holding_nb = 0

            if self.market_subtype==MARKET_SUBTYPE.SZSE_STK_GEM and self.UpLimitPx==msg_util.ORDER_PRICE_OVERFLOW and \
               ((self.TradingPhaseMarket==axsbe_base.TPM.OpenCall and \
                 (order.side==SIDE.BID and order.price>self.PrevClosePx*CYB_ORDER_ENVALUE_MAX_RATE))\
                or
                (self.TradingPhaseMarket==axsbe_base.TPM.CloseCall and \
                 (order.price>msg_util.CYB_match_upper(self.LastPx) or order.price<msg_util.CYB_match_lower(self.LastPx)))):
                self.illegal_order_map[order.applSeqNum] = order # 创业板无涨跌停时(上市头5日)超出范围则丢弃
            else:
                self.insertOrder(order)
                self.bid_waiting_for_cage = False
                self.ask_waiting_for_cage = False

            self.genSnap()   #可出snap
        else:
            # if self.holding_nb!=0: #把此前缓存的订单(市价/限价)插入LOB
            #     if self.holding_order.type == TYPE.MARKET and not self.holding_order.traded:
            #         self.ERR(f'市价单 {self.holding_order} 未伴随成交')
            #     self.insertOrder(self.holding_order)
            #     self.holding_nb = 0

            #     self._useTimestamp(self.holding_order.TransactTime)
            #     self.genSnap()   #先出一个snap，时戳用市价单的
            #     self._useTimestamp(order.TransactTime)

            if self.market_subtype==MARKET_SUBTYPE.SZSE_STK_GEM and order.type==TYPE.LIMIT and\
                (order.side==SIDE.BID and (order.price>CYB_cage_upper(self.bid_cage_ref_px)) or
                order.side==SIDE.ASK and (order.price<CYB_cage_lower(self.ask_cage_ref_px))):
                self.insertOrder(order, outOfCage=True)
                self.genSnap()   #出一个snap
            elif self.TradingPhaseMarket==axsbe_base.TPM.VolatilityBreaking:
                #波动性中断(有新order表示临停结束，正在集合竞价)，直接插入
                self.insertOrder(order)

                self.genSnap()   #可出snap
            else:
                #若是市价单或可能成交的限价单，则缓存住，等成交
                if order.type==TYPE.MARKET:
                    self.holding_order = order
                    self.holding_nb += 1
                    self.DBG('hold MARET-order')
                elif (order.side==SIDE.BID and (order.price >= self.ask_min_level_price and self.ask_min_level_qty > 0)) or \
                (order.side==SIDE.ASK and (order.price <= self.bid_max_level_price and self.bid_max_level_qty > 0)):
                    self.holding_order = order
                    self.holding_nb += 1
                    self.DBG('hold LIMIT-order')
                    self.bid_waiting_for_cage = False
                    self.ask_waiting_for_cage = False
                else:
                    self.insertOrder(order)
                    if self.market_subtype==MARKET_SUBTYPE.SZSE_STK_GEM:
                        self.enterCage()

                    self.genSnap()   #再出一个snap

    def insertOrder(self, order:ob_order, outOfCage=False):
        '''
        订单入列，更新对应的价格档位数据
        outOfCage:入列的订单不在价格笼子内（服务器将隐藏订单，不影响当前最优档）
        '''

        if outOfCage:
            self.DBG('outOfCage')

        self.order_map[order.applSeqNum] = order
        
        if order.side == SIDE.BID:
            self._export_level_access(f'LEVEL_ACCESS BID locate {order.price} //insertOrder')
            if order.price in self.bid_level_tree:
                self.bid_level_tree[order.price].qty += order.qty
                self._export_level_access(f'LEVEL_ACCESS BID writeback {order.price} //insertOrder')
                if order.price==self.bid_max_level_price:
                    self.bid_max_level_qty += order.qty
                if self.bid_cage_upper_ex_min_level_qty and order.price==self.bid_cage_upper_ex_min_level_price:
                    self.bid_cage_upper_ex_min_level_qty += order.qty
            else:
                node = level_node(order.price, order.qty, order.applSeqNum)
                self.bid_level_tree[order.price] = node
                self._export_level_access(f'LEVEL_ACCESS BID insert {order.price} //insertOrder')

                if not outOfCage:
                    if self.bid_max_level_qty==0 or order.price>self.bid_max_level_price:  #买方出现更高价格
                        self.bid_max_level_price = order.price
                        self.bid_max_level_qty = order.qty

                        self.ask_cage_ref_px = order.price
                        self.DBG(f'Ask cage ref px={self.ask_cage_ref_px}')
                        if not self.ask_min_level_qty:  #没有对手价
                            self.bid_cage_ref_px = order.price
                            self.DBG(f'bid cage ref px={self.bid_cage_ref_px}')

                        self.ask_waiting_for_cage = True if self.market_subtype==MARKET_SUBTYPE.SZSE_STK_GEM else False
                else:
                    self.DBG('Bid order out of cage.')
                    if order.price>self.bid_cage_ref_px and\
                        (self.bid_cage_upper_ex_min_level_qty==0 or order.price<self.bid_cage_upper_ex_min_level_price): #买方笼子之上出现更低价
                        self.bid_cage_upper_ex_min_level_price = order.price
                        self.bid_cage_upper_ex_min_level_qty = order.qty
                        self.DBG(f'Refresh bid_cage_upper_ex_min_level_price={self.bid_cage_upper_ex_min_level_price} by new price')

            if not outOfCage:
                self.BidWeightSize += order.qty
                self.BidWeightValue += order.price * order.qty

        elif order.side == SIDE.ASK:
            self._export_level_access(f'LEVEL_ACCESS ASK locate {order.price} //insertOrder')
            if order.price in self.ask_level_tree:
                self.ask_level_tree[order.price].qty += order.qty
                self._export_level_access(f'LEVEL_ACCESS ASK writeback {order.price} //insertOrder')
                if order.price==self.ask_min_level_price:
                    self.ask_min_level_qty += order.qty
                if self.ask_cage_lower_ex_max_level_qty and order.price==self.ask_cage_lower_ex_max_level_price:
                    self.ask_cage_lower_ex_max_level_qty += order.qty
            else:
                node = level_node(order.price, order.qty, order.applSeqNum)
                self.ask_level_tree[order.price] = node
                self._export_level_access(f'LEVEL_ACCESS ASK insert {order.price} //insertOrder')

                if order.price==PRICE_MAXIMUM:
                    self.AskWeightPx_uncertain = True #价格越界后，卖出均价将无法确定

                if not outOfCage:
                    if self.ask_min_level_qty==0 or order.price<self.ask_min_level_price: #卖方出现更低价格
                        self.ask_min_level_price = order.price
                        self.ask_min_level_qty = order.qty

                        self.bid_cage_ref_px = order.price
                        self.DBG(f'Bid cage ref px={self.bid_cage_ref_px}')
                        if not self.bid_max_level_qty:  #没有对手价
                            self.ask_cage_ref_px = order.price
                            self.DBG(f'Ask cage ref px={self.ask_cage_ref_px}')
                        self.bid_waiting_for_cage = True if self.market_subtype==MARKET_SUBTYPE.SZSE_STK_GEM else False
                else:
                    self.DBG('Ask order out of cage.')
                    if order.price<self.ask_cage_ref_px and\
                        (self.ask_cage_lower_ex_max_level_qty==0 or order.price>self.ask_cage_lower_ex_max_level_price): #买方笼子之下出现更高价
                        self.ask_cage_lower_ex_max_level_price = order.price
                        self.ask_cage_lower_ex_max_level_qty = order.qty
                        self.DBG(f'Refresh ask_cage_lower_ex_max_level_price={self.ask_cage_lower_ex_max_level_price} by new price')

            if not outOfCage:
                if self.TradingPhaseMarket==axsbe_base.TPM.OpenCall and order.price>self.PrevClosePx*CYB_ORDER_ENVALUE_MAX_RATE:  #从深交所数据上看，超过昨收(新股时为上市价)若干倍的委托不会参与统计
                    self.AskWeightSizeEx += order.qty
                    self.AskWeightValueEx += order.price * order.qty
                # if order.price<self.PrevClosePx*CYB_ORDER_ENVALUE_MAX_RATE and order.price!=(1<<PRICE_BIT_SIZE)-1:  
                else:
                    self.AskWeightSize += order.qty
                    self.AskWeightValue += order.price * order.qty

    def onExec(self, exec:axsbe_exe):
        '''
        逐笔成交入口
        跳转到处理成交或处理撤单
        '''
        self.DBG(f'msg#{self.msg_nb} onExec:{exec}')
        if exec.ExecType_str=='成交' or self.SecurityIDSource==SecurityIDSource_SSE:
            _exec = ob_exec(exec, self.instrument_type)
            self.onTrade(_exec)
        else:
            #only SecurityIDSource_SZSE
            if exec.BidApplSeqNum!=0:  # 撤销bid
                cancel_seq = exec.BidApplSeqNum
                Side = SIDE.BID
            else:   # 撤销ask
                cancel_seq = exec.OfferApplSeqNum
                Side = SIDE.ASK
            _cancel = ob_cancel(cancel_seq, exec.LastQty, exec.LastPx, Side, exec.TransactTime, self.SecurityIDSource, self.instrument_type, self.SecurityID)
            self.onCancel(_cancel)



    def onTrade(self, exec:ob_exec):
        '''处理成交消息'''
        #
        self.NumTrades += 1
        self.TotalVolumeTrade += exec.LastQty

        if self.SecurityIDSource==SecurityIDSource_SZSE:
            # 乘法输入：深圳(Qty精度2位、price精度2位or3位小数)；输出TotalValueTrade深圳(精度4位小数)
            if self.instrument_type==INSTRUMENT_TYPE.STOCK:
                self.TotalValueTrade += int(exec.LastQty * exec.LastPx/(QTY_INTER_SZSE_PRECISION*PRICE_INTER_STOCK_PRECISION // msg_util.TOTALVALUETRADE_SZSE_PRECISION)) # 2x2->4
            elif self.instrument_type==INSTRUMENT_TYPE.FUND:
                self.TotalValueTrade += int(exec.LastQty * exec.LastPx/(QTY_INTER_SZSE_PRECISION*PRICE_INTER_FUND_PRECISION // msg_util.TOTALVALUETRADE_SZSE_PRECISION)) # 2x3->4
            elif self.instrument_type==INSTRUMENT_TYPE.KZZ:
                self.TotalValueTrade += int(exec.LastQty * exec.LastPx/(QTY_INTER_SZSE_PRECISION*PRICE_INTER_KZZ_PRECISION // msg_util.TOTALVALUETRADE_SZSE_PRECISION)) # 2x3->4
            else:
                self.TotalValueTrade += None
        elif self.SecurityIDSource==SecurityIDSource_SSE:
            # 乘法输入：上海(Qty精度3位、price精度2位or3位小数)；输出TotalValueTrade上海(精度5位小数)
            if self.instrument_type==INSTRUMENT_TYPE.STOCK:
                self.TotalValueTrade += int(exec.LastQty * exec.LastPx/(QTY_INTER_SSE_PRECISION*PRICE_INTER_STOCK_PRECISION // msg_util.TOTALVALUETRADE_SSE_PRECISION)) # 3x2 -> 5
            elif self.instrument_type==INSTRUMENT_TYPE.FUND:
                self.TotalValueTrade += int(exec.LastQty * exec.LastPx/(QTY_INTER_SSE_PRECISION*PRICE_INTER_FUND_PRECISION // msg_util.TOTALVALUETRADE_SZSE_PRECISION)) # 3x3->5
            else:
                self.TotalValueTrade += None
        else:
            self.TotalValueTrade += None

        self.LastPx = exec.LastPx
        if self.OpenPx == 0:
            self.OpenPx = exec.LastPx
            self.HighPx = exec.LastPx
            self.LowPx = exec.LastPx
        else:
            if self.HighPx < exec.LastPx:
                self.HighPx = exec.LastPx
            if self.LowPx > exec.LastPx:
                self.LowPx = exec.LastPx

        #有可能市价单剩余部分进队列，后续成交是由价格笼子外的订单造成的
        if self.holding_nb and self.holding_order.type==TYPE.MARKET:
            if self.holding_order.applSeqNum!=exec.BidApplSeqNum and self.holding_order.applSeqNum!=exec.OfferApplSeqNum:
                self.WARN('MARKET order followed by unmatch exec, take as traded over!')
                assert self.market_subtype==MARKET_SUBTYPE.SZSE_STK_GEM, f'{self.SecurityID:06d} not CYB'
                self.insertOrder(self.holding_order)
                self.holding_nb = 0

                self._useTimestamp(self.holding_order.TransactTime)
                self.genSnap()   #先出一个snap，时戳用市价单的
                self._useTimestamp(exec.TransactTime)
                

        if self.holding_nb!=0:
            # 紧跟缓存单的成交
            level_side = SIDE.ASK if exec.BidApplSeqNum==self.holding_order.applSeqNum else SIDE.BID #level_side:缓存单的对手盘
            self.DBG(f'level_side={level_side}')
            assert self.holding_order.qty>=exec.LastQty, f"{self.SecurityID:06d} holding order Qty unmatch"
            if self.holding_order.qty==exec.LastQty:
                self.holding_nb = 0
            else:
                self.holding_order.qty -= exec.LastQty

                if self.holding_order.type==TYPE.MARKET:   #修改市价单的价格
                    self.holding_order.price = exec.LastPx
                    self.holding_order.traded = True

            if level_side==SIDE.ASK:
                self.tradeLimit(SIDE.ASK, exec.LastQty, exec.OfferApplSeqNum)
            else:
                self.tradeLimit(SIDE.BID, exec.LastQty, exec.BidApplSeqNum)

            if self.holding_nb!=0 and self.holding_order.type==TYPE.LIMIT:  #检查限价单是否还有对手价
                if (self.holding_order.side==SIDE.BID and (self.holding_order.price<self.ask_min_level_price or self.ask_min_level_qty==0)) or \
                   (self.holding_order.side==SIDE.ASK and (self.holding_order.price>self.bid_max_level_price or self.bid_max_level_qty==0)):
                   # 对手盘已空，缓存单入列
                    self.insertOrder(self.holding_order)
                    self.holding_nb = 0

            if self.market_subtype==MARKET_SUBTYPE.SZSE_STK_GEM:
                self.enterCage()

            if self.holding_nb==0:
                self.genSnap()   #缓存单成交完
        elif self.bid_waiting_for_cage or self.ask_waiting_for_cage:
            self.DBG("Order entered cage & exec.")
            self.tradeLimit(SIDE.ASK, exec.LastQty, exec.OfferApplSeqNum)
            self.tradeLimit(SIDE.BID, exec.LastQty, exec.BidApplSeqNum)
            if self.market_subtype==MARKET_SUBTYPE.SZSE_STK_GEM:
                self.enterCage()
            self.genSnap()   #出一个snap
        else:
            assert self.holding_nb==0, f'{self.SecurityID:06d} unexpected exec while holding_nb!=0'
            #20221010 300654  碰到深交所订单乱序：先发送2档以上的逐笔成交，再发送1档的撤单（卖方1档撤单导致买方订单进入价格笼子，吃掉卖方2档及以上）；目前直接应用成交可以正常继续重建:
            if not ((exec.TransactTime%SZSE_TICK_CUT==92500000)or(exec.TransactTime%SZSE_TICK_CUT==150000000) if self.SecurityIDSource==SecurityIDSource_SZSE else (exec.TransactTime==9250000)or(exec.TransactTime==15000000)) and\
               self.TradingPhaseMarket!=axsbe_base.TPM.VolatilityBreaking:
                self.WARN(f'unexpected exec @{exec.TransactTime}!')

            self.tradeLimit(SIDE.ASK, exec.LastQty, exec.OfferApplSeqNum)
            self.tradeLimit(SIDE.BID, exec.LastQty, exec.BidApplSeqNum)

            if self.ask_min_level_qty==0 or self.bid_max_level_qty==0 or self.ask_min_level_price>self.bid_max_level_price:
                self.DBG('openCall/closeCall trade over')
                if self.TradingPhaseMarket==axsbe_base.TPM.VolatilityBreaking:
                    self.TradingPhaseMarket = exec.TradingPhaseMarket
                self.genSnap()   #集合竞价所有成交完成

    def enterCage(self):
        '''判断订单是否可进入笼子，若进入笼子，判断是否可以成交'''
        while True:
            if self.bid_cage_upper_ex_min_level_qty and self.bid_cage_upper_ex_min_level_price<=CYB_cage_upper(self.bid_cage_ref_px): #买方隐藏订单可以进入笼子
                if self.ask_min_level_qty and self.bid_cage_upper_ex_min_level_price>=self.ask_min_level_price and self.TradingPhaseMarket!=axsbe_base.TPM.VolatilityBreaking: #可与卖方最优成交
                    self.DBG(f'ASK px may changed: waiting for BID level' 
                             f'({self.bid_cage_upper_ex_min_level_price} x {self.bid_cage_upper_ex_min_level_qty}) to enter cage & exec')
                    break
                else:   #无法成交，将隐藏订单加到买方队列
                    self.bid_max_level_price = self.bid_cage_upper_ex_min_level_price
                    self.bid_max_level_qty = self.bid_cage_upper_ex_min_level_qty
                    self.BidWeightSize += self.bid_cage_upper_ex_min_level_qty
                    self.BidWeightValue += self.bid_cage_upper_ex_min_level_price * self.bid_cage_upper_ex_min_level_qty
                    self.DBG('BID order enter cage and became max level')
                    
                    self.ask_cage_ref_px = self.bid_max_level_price
                    self.DBG(f'ASK cage ref px={self.ask_cage_ref_px}')
                    if not self.ask_min_level_qty:
                        self.bid_cage_ref_px = self.bid_max_level_price
                        self.DBG(f'Bid cage ref px={self.bid_cage_ref_px}')

                    self.ask_waiting_for_cage = True if self.market_subtype==MARKET_SUBTYPE.SZSE_STK_GEM else False   #买方最优价被修改，则判断卖方隐藏订单

                    #下一个隐藏订单，继续循环，直到无隐藏订单、隐藏订单可成交
                    self.bid_cage_upper_ex_min_level_qty = 0
                    self._export_level_access(f'LEVEL_ACCESS BID locate_higher {self.bid_cage_upper_ex_min_level_price} //enterCage:find next order out of cage')
                    for p, l in sorted(self.bid_level_tree.items(),key=lambda x:x[0], reverse=False):    #从小到大遍历
                        if p>self.bid_cage_upper_ex_min_level_price:
                            self.bid_cage_upper_ex_min_level_price = p
                            self.bid_cage_upper_ex_min_level_qty = l.qty
                            self.DBG(f'Refresh bid_cage_upper_ex_min_level_price={self.bid_cage_upper_ex_min_level_price} by prev bid level enter cage')
                            break
            else:
                self.bid_waiting_for_cage = False

            if self.ask_cage_lower_ex_max_level_qty and self.ask_cage_lower_ex_max_level_price>=CYB_cage_lower(self.ask_cage_ref_px): #卖方隐藏订单可以进入笼子
                if self.bid_max_level_qty and self.ask_cage_lower_ex_max_level_price<=self.bid_max_level_price and self.TradingPhaseMarket!=axsbe_base.TPM.VolatilityBreaking: #可与买方最优成交
                    self.DBG(f'BID px may changed: waiting for ASK level'
                             f'({self.ask_cage_lower_ex_max_level_price} x {self.ask_cage_lower_ex_max_level_qty}) to enter cage & exec')
                    break
                else:   #无法成交，将隐藏订单加到买方队列
                    self.ask_min_level_price = self.ask_cage_lower_ex_max_level_price
                    self.ask_min_level_qty = self.ask_cage_lower_ex_max_level_qty
                    self.AskWeightSize += self.ask_cage_lower_ex_max_level_qty
                    self.AskWeightValue += self.ask_cage_lower_ex_max_level_price * self.ask_cage_lower_ex_max_level_qty
                    self.DBG('ASK order enter cage and became min level')

                    self.bid_cage_ref_px = self.ask_min_level_price
                    self.DBG(f'BID cage ref px={self.bid_cage_ref_px}')
                    if not self.bid_max_level_qty:
                        self.ask_cage_ref_px = self.ask_min_level_price
                        self.DBG(f'Ask cage ref px={self.ask_cage_ref_px}')

                    self.bid_waiting_for_cage = True if self.market_subtype==MARKET_SUBTYPE.SZSE_STK_GEM else False   #卖方最优价被修改，则判断买方隐藏订单

                    self.ask_cage_lower_ex_max_level_qty = 0
                    self._export_level_access(f'LEVEL_ACCESS ASK locate_lower {self.ask_cage_lower_ex_max_level_price} //enterCage:find next order out of cage')
                    for p, l in sorted(self.ask_level_tree.items(),key=lambda x:x[0], reverse=True):    #从大到小遍历
                        if p<self.ask_cage_lower_ex_max_level_price:
                            self.ask_cage_lower_ex_max_level_price = p
                            self.ask_cage_lower_ex_max_level_qty = l.qty
                            self.DBG(f'Refresh ask_cage_lower_ex_max_level_price={self.ask_cage_lower_ex_max_level_price} by prev ask level enter cage')
                            break
            else:
                self.ask_waiting_for_cage = False

            if not self.bid_waiting_for_cage and not self.ask_waiting_for_cage:
                break


    def tradeLimit(self, side:SIDE, Qty, appSeqNum):
        if appSeqNum not in self.order_map:
            self.ERR(f'traded order #{appSeqNum} not found!')
        order = self.order_map[appSeqNum]
        # order.qty -= Qty
        self.levelDequeue(side, order.price, Qty, appSeqNum)

    def onCancel(self, cancel:ob_cancel):
        '''
        处理撤单，来自深交所逐笔成交或上交所逐笔成交
        撤销此前缓存的订单(市价/限价)，或插入LOB
        '''
        if self.holding_nb!=0:    #此处缓存的应该都是市价单
            self.holding_nb = 0

            if True:
                ## 仅测试：不论撤销的是不是缓存单，都将缓存单插入OB并生成快照用于比较
                ##  因为市场快照可能是缓存单插入后的快照
                self.insertOrder(self.holding_order)
                if cancel.TransactTime!=self.holding_order.TransactTime:    #这个if是为了规避 最优五档即时成交剩余撤销申报 在撤单时没有发生过成交但插入的价格不对的问题，切换到实际操作是不会有这个问题。
                    self._useTimestamp(self.holding_order.TransactTime)
                    self.genSnap()   #先出一个snap，时戳用缓存单(市价单)的
                    self._useTimestamp(cancel.TransactTime)

            else:
                ## 实际操作，如果撤销的是缓存单，则不需要插入OB：
                if self.holding_order.applSeqNum!=cancel.applSeqNum: #撤销的不是缓存单，把缓存单插入LOB
                    self.insertOrder(self.holding_order)
                    self._useTimestamp(self.holding_order.TransactTime)
                    self.genSnap()   #先出一个snap，时戳用缓存单(市价单)的
                    self._useTimestamp(cancel.TransactTime)
                if self.holding_order.applSeqNum==cancel.applSeqNum: #撤销缓存单，holding_nb清空即可
                    return  

        if cancel.applSeqNum in self.order_map:
            order = self.order_map.pop(cancel.applSeqNum)   # 注意order.qty是旧值。实际可以不用pop。

            self.levelDequeue(cancel.side, order.price, cancel.qty, cancel.applSeqNum)
            if self.market_subtype==MARKET_SUBTYPE.SZSE_STK_GEM:
                self.enterCage()

            self.genSnap()
        elif cancel.applSeqNum in self.illegal_order_map:
            self.illegal_order_map.pop(cancel.applSeqNum)
        else:
            self.ERR(f'cancel AppSeqNum={cancel.applSeqNum} not found!')
            raise 'cancel AppSeqNum not found!'

    def levelDequeue(self, side, price, qty, applSeqNum):
        '''买/卖方价格档出列（撤单或成交时）'''
        if side == SIDE.BID:
            self.bid_level_tree[price].qty -= qty
            self._export_level_access(f'LEVEL_ACCESS BID locate {price} //levelDequeue')
            # self.bid_level_tree[price].ts.remove(applSeqNum)
            if price==self.bid_max_level_price:
                self.bid_max_level_qty -= qty

            if self.bid_cage_upper_ex_min_level_qty==0 or price<self.bid_cage_upper_ex_min_level_price:
                self.BidWeightSize -= qty
                self.BidWeightValue -= price * qty
            elif self.bid_cage_upper_ex_min_level_qty and price==self.bid_cage_upper_ex_min_level_price:
                self.bid_cage_upper_ex_min_level_qty -= qty
                if self.bid_cage_upper_ex_min_level_qty==0: #买方价格笼子外最低价被cancel/trade光
                    # locate next high bid level
                    self._export_level_access(f'LEVEL_ACCESS BID locate_higher {self.bid_cage_upper_ex_min_level_price} //levelDequeue:find next level out of cage')
                    for p, l in sorted(self.bid_level_tree.items(),key=lambda x:x[0], reverse=False):    #从小到大遍历，TODO:可以先判断是否存在
                        if p>self.bid_cage_upper_ex_min_level_price:
                            self.bid_cage_upper_ex_min_level_price = p
                            self.bid_cage_upper_ex_min_level_qty = l.qty
                            self.DBG(f'Refresh bid_cage_upper_ex_min_level_price={self.bid_cage_upper_ex_min_level_price} by canceled/traded all')
                            break

            if self.bid_level_tree[price].qty==0:
                if price==self.bid_max_level_price:  #买方最高价被cancel/trade光
                    self.bid_max_level_qty = 0
                    # locate next lower bid level
                    self._export_level_access(f'LEVEL_ACCESS BID locate_lower {self.bid_max_level_price} //levelDequeue:find next side level')
                    for p, l in sorted(self.bid_level_tree.items(),key=lambda x:x[0], reverse=True):    #从大到小遍历
                        if p<self.bid_max_level_price:
                            self.bid_max_level_price = p
                            self.bid_max_level_qty = l.qty
                            break

                    # 修改卖方价格笼子参考价
                    if self.bid_max_level_qty!=0:                       # 买方还有下一档
                        self.ask_cage_ref_px = self.bid_max_level_price
                    else:
                        self._export_level_access(f'LEVEL_ACCESS ASK locate {price} //levelDequeue:update oppo ref px')
                        if price in self.ask_level_tree:             # 卖方本价位有量(此时ask_min_level_price可能是旧的)
                            self.ask_cage_ref_px = price                    #TODO: 卖方hold?
                        elif self.ask_min_level_qty!=0:
                            self.ask_cage_ref_px = self.ask_min_level_price
                        else:
                            self.ask_cage_ref_px = self.LastPx # 一旦lastPx被更新，总会到这里，而此后就不会再用PreClosePx了
                    self.DBG(f'Ask cage ref px={self.ask_cage_ref_px}')
                    
                    if self.TradingPhaseMarket==axsbe_base.TPM.AMTrading or self.TradingPhaseMarket==axsbe_base.TPM.PMTrading:
                        self.ask_waiting_for_cage = True if self.market_subtype==MARKET_SUBTYPE.SZSE_STK_GEM else False
                    else:
                        self.ask_waiting_for_cage = False
                
                #remove要在locate_lower之后
                self.bid_level_tree.pop(price)
                self._export_level_access(f'LEVEL_ACCESS BID remove {price} //levelDequeue')
            else:
                self._export_level_access(f'LEVEL_ACCESS BID writeback {price} //levelDequeue')

        else:## side == SIDE.ASK:
            self.ask_level_tree[price].qty -= qty
            self._export_level_access(f'LEVEL_ACCESS ASK locate {price} //levelDequeue')
            # self.ask_level_tree[price].ts.remove(applSeqNum)
            if price==self.ask_min_level_price:
                self.ask_min_level_qty -= qty

            if (self.ask_cage_lower_ex_max_level_qty==0 or price>self.ask_cage_lower_ex_max_level_price):
                if self.TradingPhaseMarket==axsbe_base.TPM.OpenCall and price>self.PrevClosePx*CYB_ORDER_ENVALUE_MAX_RATE: #从深交所数据上看，超过昨收(新股时为上市价)若干倍的委托不会参与统计
                    self.AskWeightSizeEx -= qty
                    self.AskWeightValueEx -= price * qty
                else:
                    self.AskWeightSize -= qty
                    self.AskWeightValue -= price * qty
            elif self.ask_cage_lower_ex_max_level_qty and price==self.ask_cage_lower_ex_max_level_price:
                self.ask_cage_lower_ex_max_level_qty -= qty
                if self.ask_cage_lower_ex_max_level_qty==0: #卖方价格笼子外最高价被cancel/trade光
                    # locate next high bid level
                    self._export_level_access(f'LEVEL_ACCESS ASK locate_lower {self.ask_cage_lower_ex_max_level_price} //levelDequeue:find next level out of cage')
                    for p, l in sorted(self.ask_level_tree.items(),key=lambda x:x[0], reverse=True):    #从大到小遍历，TODO:可以先判断是否存在
                        if p<self.ask_cage_lower_ex_max_level_price:
                            self.ask_cage_lower_ex_max_level_price = p
                            self.ask_cage_lower_ex_max_level_qty = l.qty
                            self.DBG(f'Refresh ask_cage_lower_ex_max_level_price={self.ask_cage_lower_ex_max_level_price} by canceled/traded all')
                            break


            if self.ask_level_tree[price].qty==0:
                if price==PRICE_MAXIMUM:
                    self.AskWeightPx_uncertain = False #加权价又可确定了

                if price==self.ask_min_level_price:  #卖方最低价被cancel/trade光
                    # locate next higher ask level
                    self.ask_min_level_qty = 0
                    self._export_level_access(f'LEVEL_ACCESS ASK locate_higher {self.ask_min_level_price} //levelDequeue:find next side level')
                    for p, l in sorted(self.ask_level_tree.items(),key=lambda x:x[0], reverse=False):    #从小到大遍历
                        if p>self.ask_min_level_price:
                            self.ask_min_level_price = p
                            self.ask_min_level_qty = l.qty
                            break

                    # 修改买方价格笼子参考价
                    if self.ask_min_level_qty!=0:                           # 卖方还有下一档
                        self.bid_cage_ref_px = self.ask_min_level_price
                    else:
                        self._export_level_access(f'LEVEL_ACCESS BID locate {price} //levelDequeue:update oppo ref px')
                        if price in self.bid_level_tree:             # 买方本价位有量(此时bid_max_level_price可能是旧的)
                            self.bid_cage_ref_px = price                    #TODO: 买方hold?
                        elif self.bid_max_level_qty!=0:
                            self.bid_cage_ref_px = self.bid_max_level_price
                        else:
                            self.bid_cage_ref_px = self.LastPx # 一旦lastPx被更新，总会到这里，而此后就不会再用PreClosePx了
                    self.DBG(f'Bid cage ref px={self.bid_cage_ref_px}')

                    if self.TradingPhaseMarket==axsbe_base.TPM.AMTrading or self.TradingPhaseMarket==axsbe_base.TPM.PMTrading:
                        self.bid_waiting_for_cage = True if self.market_subtype==MARKET_SUBTYPE.SZSE_STK_GEM else False
                    else:
                        self.bid_waiting_for_cage = False
                
                #remove要在locate_lower之后
                self.ask_level_tree.pop(price)
                self._export_level_access(f'LEVEL_ACCESS ASK remove {price} //levelDequeue')
            else:
                self._export_level_access(f'LEVEL_ACCESS ASK writeback {price} //levelDequeue')


    def onSnap(self, snap:axsbe_snap_stock):
        self.DBG(f'msg#{self.msg_nb} onSnap:{snap}')
        if snap.TradingPhaseSecurity != axsbe_base.TPI.Normal:
            if self.SecurityIDSource==SecurityIDSource_SZSE: #深交所：当天可交易的始终都是可交易
                self.ERR(f'TradingPhaseSecurity={axsbe_base.TPI.str(snap.TradingPhaseSecurity)}@{snap.HHMMSSms}')
                return
            elif self.SecurityIDSource==SecurityIDSource_SSE:#上交所：股票/基金9点14都还是不可交易
                self.INFO(f'TradingPhaseSecurity={axsbe_base.TPI.str(snap.TradingPhaseSecurity)}@{snap.HHMMSSms}')

        ## 更新常量
        if snap.TradingPhaseMarket==axsbe_base.TPM.Starting: # 每天最早的一批快照(7点半前)是没有涨停价、跌停价的，不能只锁一次
            self.constantValue_ready = True
            if self.ChannelNo==CHANNELNO_INIT:
                self.DBG(f"Update constatant: ChannelNo={snap.ChannelNo}, PrevClosePx={snap.PrevClosePx}, UpLimitPx={snap.UpLimitPx}, DnLimitPx={snap.DnLimitPx}")

            self.ChannelNo = snap.ChannelNo
            if self.SecurityIDSource==SecurityIDSource_SZSE:
                if self.instrument_type==INSTRUMENT_TYPE.STOCK:
                    self.PrevClosePx = snap.PrevClosePx // (msg_util.PRICE_SZSE_SNAP_PRECLOSE_PRECISION//PRICE_INTER_STOCK_PRECISION)
                elif self.instrument_type==INSTRUMENT_TYPE.FUND:
                    self.PrevClosePx = snap.PrevClosePx // (msg_util.PRICE_SZSE_SNAP_PRECLOSE_PRECISION//PRICE_INTER_FUND_PRECISION)
                elif self.instrument_type==INSTRUMENT_TYPE.KZZ:
                    self.PrevClosePx = snap.PrevClosePx // (msg_util.PRICE_SZSE_SNAP_PRECLOSE_PRECISION//PRICE_INTER_KZZ_PRECISION)
                else:
                    raise Exception(f'instrument_type={self.instrument_type} is not ready!')    # TODO:
            elif self.SecurityIDSource==SecurityIDSource_SSE:
                if self.instrument_type==INSTRUMENT_TYPE.STOCK:
                    self.PrevClosePx = snap.PrevClosePx // (msg_util.PRICE_SSE_PRECISION//PRICE_INTER_STOCK_PRECISION)
                elif self.instrument_type==INSTRUMENT_TYPE.FUND:
                    self.PrevClosePx = snap.PrevClosePx // (msg_util.PRICE_SSE_PRECISION//PRICE_INTER_FUND_PRECISION)
                elif self.instrument_type==INSTRUMENT_TYPE.BOND:
                    self.PrevClosePx = 0 # 上海债券快照没有带昨收！
                else:
                    raise Exception(f'instrument_type={self.instrument_type} is not ready!')    #
            else:
                raise Exception(f'SecurityIDSource={self.SecurityIDSource} is not ready!')    # TODO:

            if self.SecurityIDSource==SecurityIDSource_SZSE:
                self.ask_cage_ref_px = self.PrevClosePx
                self.bid_cage_ref_px = self.PrevClosePx
                self.DBG(f'Init Bid cage ref px={self.bid_cage_ref_px}')

                self.UpLimitPx = snap.UpLimitPx
                self.DnLimitPx = snap.DnLimitPx
                
                if self.SecurityIDSource==SecurityIDSource_SZSE:
                    if self.instrument_type==INSTRUMENT_TYPE.STOCK:
                        self.UpLimitPrice = snap.UpLimitPx // (msg_util.PRICE_SZSE_SNAP_PRECISION//PRICE_INTER_STOCK_PRECISION)
                        self.DnLimitPrice = snap.DnLimitPx // (msg_util.PRICE_SZSE_SNAP_PRECISION//PRICE_INTER_STOCK_PRECISION)
                    elif self.instrument_type==INSTRUMENT_TYPE.FUND:
                        self.UpLimitPrice = snap.UpLimitPx // (msg_util.PRICE_SZSE_SNAP_PRECISION//PRICE_INTER_FUND_PRECISION)
                        self.DnLimitPrice = snap.DnLimitPx // (msg_util.PRICE_SZSE_SNAP_PRECISION//PRICE_INTER_FUND_PRECISION)
                    elif self.instrument_type==INSTRUMENT_TYPE.KZZ:
                        self.UpLimitPrice = snap.UpLimitPx // (msg_util.PRICE_SZSE_SNAP_PRECISION//PRICE_INTER_KZZ_PRECISION)
                        self.DnLimitPrice = snap.DnLimitPx // (msg_util.PRICE_SZSE_SNAP_PRECISION//PRICE_INTER_KZZ_PRECISION)
                    else:
                        raise Exception(f'instrument_type={self.instrument_type} is not ready!')    # TODO:
            elif self.SecurityIDSource==SecurityIDSource_SSE:
                pass
            else:
                raise Exception(f'SecurityIDSource={self.SecurityIDSource} is not ready!')    # TODO:

            if self.SecurityIDSource==SecurityIDSource_SZSE:
                self.YYMMDD = snap.TransactTime // SZSE_TICK_CUT # 深交所带日期
            else:
                self.YYMMDD = 0                               # 上交所不带日期

        if self.TradingPhaseMarket==axsbe_base.TPM.Ending and snap.TradingPhaseMarket==axsbe_base.TPM.Ending and not self.closePx_ready:
            if self.SecurityIDSource==SecurityIDSource_SZSE:
                if self.instrument_type==INSTRUMENT_TYPE.STOCK:
                    self.LastPx = snap.LastPx // (msg_util.PRICE_SZSE_SNAP_PRECISION//PRICE_INTER_STOCK_PRECISION)
                elif self.instrument_type==INSTRUMENT_TYPE.FUND:
                    self.LastPx = snap.LastPx // (msg_util.PRICE_SZSE_SNAP_PRECISION//PRICE_INTER_FUND_PRECISION)
                elif self.instrument_type==INSTRUMENT_TYPE.KZZ:
                    self.LastPx = snap.LastPx // (msg_util.PRICE_SZSE_SNAP_PRECISION//PRICE_INTER_KZZ_PRECISION)
                else:
                    pass    # TODO:
            else:
                self.ERR('SSE ClosePx not checked!')

            self.closePx_ready = True
            self.genSnap()

        if snap.TradingPhaseMarket==axsbe_base.TPM.VolatilityBreaking and self.TradingPhaseMarket!=axsbe_base.TPM.VolatilityBreaking:  #进入波动性中断
            self.WARN(f'Enter VolatilityBreaking @{snap.TransactTime}')
            # self.VolatilityBreaking_end_tick = 0
            self.TradingPhaseMarket = axsbe_base.TPM.VolatilityBreaking
            self.genSnap()

        ## 检查重建算法，仅用于测试算法是否正确：
        snap._seq = self.msg_nb
        if (self.SecurityIDSource==SecurityIDSource_SZSE and snap.TradingPhaseMarket<axsbe_base.TPM.OpenCall) \
         or(self.SecurityIDSource==SecurityIDSource_SSE and snap.TradingPhaseMarket<axsbe_base.TPM.PreTradingBreaking):
            # 深交所: 从开盘集合竞价开始生成快照，之前的不记录
            # 上交所：从开盘集合竞价后休市开始生成快照，之前的不记录
            pass
        else:
            # 在重建的快照中检索是否有相同的快照
            if self.last_snap and snap.is_same(self.last_snap) and self._chkSnapTimestamp(snap, self.last_snap):
                self.DBG(f'market snap #{self.msg_nb}({snap.TransactTime})'+
                          f' matches last rebuilt snap #{self.last_snap._seq}({self.last_snap.TransactTime})')
                ks = list(self.rebuilt_snaps.keys())
                for k in ks:
                    if k < snap.NumTrades:
                        self.rebuilt_snaps.pop(k)
                #这里不丢弃last_snap，因为可能无逐笔数据而导致快照不更新
            else:
                matched = False
                if snap.NumTrades in self.rebuilt_snaps:
                    for gen in self.rebuilt_snaps[snap.NumTrades]:
                        if snap.is_same(gen) and self._chkSnapTimestamp(snap, gen):
                            self.DBG(f'market snap #{self.msg_nb}({snap.TransactTime})'+
                                    f' matches history rebuilt snap #{gen._seq}({gen.TransactTime})')
                            matched = True
                            break
                
                if matched:
                    ks = list(self.rebuilt_snaps.keys())
                    for k in ks:
                        if k < snap.NumTrades:
                            self.rebuilt_snaps.pop(k)
                else:
                    if snap.NumTrades not in self.market_snaps:
                        self.market_snaps[snap.NumTrades] = [snap]
                    else:
                        self.market_snaps[snap.NumTrades].append(snap) #缓存交易所快照
                    self.WARN(f'market snap #{self.msg_nb}({snap.TransactTime}) not found in history rebuilt snaps!')


    def genSnap(self):
        assert self.TradingPhaseMarket==axsbe_base.TPM.VolatilityBreaking or self.holding_nb==0, f'{self.SecurityID:06d} genSnap but with holding'

        snap = None
        if self.TradingPhaseMarket < axsbe_base.TPM.OpenCall or self.TradingPhaseMarket > axsbe_base.TPM.Ending:
            # 无需生成
            pass
        elif self.TradingPhaseMarket==axsbe_base.TPM.OpenCall or self.TradingPhaseMarket==axsbe_base.TPM.CloseCall:
            # 集合竞价快照
            snap = self.genCallSnap()
        elif self.TradingPhaseMarket==axsbe_base.TPM.VolatilityBreaking:
            snap = self.genTradingSnap(isVolatilityBreaking=True)
        elif self.TradingPhaseMarket==axsbe_base.TPM.Ending:
            if self.closePx_ready: #收盘价已经ready
                snap = self.genTradingSnap()
        else:
            # 连续竞价快照
            snap = self.genTradingSnap()

        if snap is not None:
            snap.AskWeightPx_uncertain = self.AskWeightPx_uncertain
            self._clipSnap(snap)

        ## 调试数据，仅用于测试算法是否正确：
        if snap is not None:
            self.DBG(snap)

            if (snap.TradingPhaseMarket==axsbe_base.TPM.AMTrading or snap.TradingPhaseMarket==axsbe_base.TPM.PMTrading) and\
                len(snap.ask)>0 and snap.ask[0].Qty and len(snap.bid) and snap.bid[0].Qty:
                assert snap.ask[0].Price>snap.bid[0].Price, f'{self.SecurityID:06d} bid.max({snap.bid[0].Price})/ask.min({snap.ask[0].Price}) NG'

            snap._seq = self.msg_nb # 用于调试
            self.last_snap = snap

            #在收到的交易所快照中查找是否有一样的,允许匹配多个快照
            matched = []
            if snap.NumTrades in self.market_snaps:
                for rcv in self.market_snaps[snap.NumTrades]:
                    if snap.is_same(rcv) and self._chkSnapTimestamp(rcv, snap):
                        self.WARN(f'rebuilt snap #{snap._seq}({snap.TransactTime}) matches history market snap #{rcv._seq}({rcv.TransactTime})') # 重建快照在市场快照之后，属于警告
                        matched.append(rcv)
            
            if len(matched): 
                for rcv in matched:
                    self.market_snaps[snap.NumTrades].remove(rcv)    #丢弃已匹配的
                if len(self.market_snaps[snap.NumTrades])==0:
                    self.market_snaps.pop(snap.NumTrades)

            # 总是缓存生成的快照，因为可能要跟多个市场快照匹配
            if snap.NumTrades not in self.rebuilt_snaps:
                self.rebuilt_snaps[snap.NumTrades] = [snap]
            else:
                self.rebuilt_snaps[snap.NumTrades].append(snap)


    def _setSnapFixParam(self, snap):
        '''固定参数:每日开盘集合竞价前确定'''
        snap.SecurityID = self.SecurityID
        if self.SecurityIDSource==SecurityIDSource_SZSE:
            if self.instrument_type==INSTRUMENT_TYPE.STOCK:
                snap.PrevClosePx = self.PrevClosePx * (msg_util.PRICE_SZSE_SNAP_PRECLOSE_PRECISION//PRICE_INTER_STOCK_PRECISION)
            elif self.instrument_type==INSTRUMENT_TYPE.FUND:
                snap.PrevClosePx = self.PrevClosePx * (msg_util.PRICE_SZSE_SNAP_PRECLOSE_PRECISION//PRICE_INTER_FUND_PRECISION)
            elif self.instrument_type==INSTRUMENT_TYPE.KZZ:
                snap.PrevClosePx = self.PrevClosePx * (msg_util.PRICE_SZSE_SNAP_PRECLOSE_PRECISION//PRICE_INTER_KZZ_PRECISION)
            else:
                snap.PrevClosePx = self.PrevClosePx    #TODO:
        else:
            '''TODO-SSE'''
            
        snap.UpLimitPx = self.UpLimitPx
        snap.DnLimitPx = self.DnLimitPx
        snap.ChannelNo = self.ChannelNo

    def _clipSnap(self, snap):
        '''超大数据钳位'''
        snap.AskWeightPx = self._clipInt32(snap.AskWeightPx) #当委托价无上限时，加权价格可能超出32位整数，也没有什么意义了，直接钳位到最大

    def _useTimestamp(self, TransactTime):
        if self.SecurityIDSource == SecurityIDSource_SZSE:
            self.current_inc_tick = TransactTime // SZSE_TICK_MS_TAIL % (SZSE_TICK_CUT // SZSE_TICK_MS_TAIL)    #只用逐笔 (10ms精度) 15000000 24b
        else:
            self.current_inc_tick = TransactTime # 上交所(1ms精度) 150000000
        if self.current_inc_tick >= (1<<TIMESTAMP_BIT_SIZE):
            self.ERR(f'msg.TransactTime={TransactTime} ovf!')


    def _setSnapTimestamp(self, snap):
        if self.SecurityIDSource==SecurityIDSource_SZSE:
            snap.TransactTime = self.YYMMDD * SZSE_TICK_CUT + (self.current_inc_tick*SZSE_TICK_MS_TAIL) #深交所显示精度到ms，多补1位
        elif self.SecurityIDSource==SecurityIDSource_SZSE:
            if self.instrument_type==INSTRUMENT_TYPE.BOND or self.instrument_type==INSTRUMENT_TYPE.KZZ or self.instrument_type==INSTRUMENT_TYPE.NHG:
                snap.TransactTime = self.current_inc_tick #债券精确到ms
            else:
                snap.TransactTime = self.current_inc_tick // 100 #上交所只显示到秒，去掉10ms和100ms两位



    def genCallSnap(self, show_level_nb=10, show_potential=False):
        '''
        show_level_nb:  展示的价格档数
        show_potential: 在无法撮合时展示出双方价格档
        '''
        # if self.msg_nb>=885:
        #    self._print_levels()
        #1. 查找 最低卖出价格档、最高买入价格档
        _bid_max_level_price = self.bid_max_level_price
        _bid_max_level_qty = self.bid_max_level_qty
        _ask_min_level_price = self.ask_min_level_price
        _ask_min_level_qty = self.ask_min_level_qty

        #2. 初始 撮合成交价
        if _bid_max_level_qty==0 and _ask_min_level_qty==0: #两边都无委托
            price = 0
        else: # 至少一边存在委托
            if _bid_max_level_qty==0:
                price = _ask_min_level_price
            elif _ask_min_level_qty==0:
                price = _bid_max_level_price
            else:   #两边都存在，双方最优价可能交叉也可能无交叉
                price = 0

        
        #3. 初始 总成交数量 = 0
        volumeTrade = 0
        bid_Qty = 0
        bid_trade_level_nb = 0
        ask_Qty = 0
        ask_trade_level_nb = 0
        
        #4. 撮合循环：
        _ref_px = self.PrevClosePx if self.NumTrades==0 else self.LastPx
        while True:  # 
            if _bid_max_level_qty!=0 and _ask_min_level_qty!=0 and _bid_max_level_price >= _ask_min_level_price:    # 双方均有最优委托 且 双方最优价有交叉
                if bid_Qty == 0:
                    bid_Qty = _bid_max_level_qty
                if ask_Qty == 0:
                    ask_Qty = _ask_min_level_qty
                if bid_Qty >= ask_Qty:
                    volumeTrade += ask_Qty
                    bid_Qty -= ask_Qty
                    ask_Qty = 0

                    ask_trade_level_nb += 1
                    if bid_Qty==ask_Qty:
                        bid_trade_level_nb += 1
                else:
                    volumeTrade += bid_Qty
                    ask_Qty -= bid_Qty
                    bid_Qty = 0

                    bid_trade_level_nb += 1

                if bid_Qty == 0 and ask_Qty == 0:   # 恰好双方数量相等。 
                    if _bid_max_level_price>=_ref_px and _ask_min_level_price<=_ref_px:   #
                        price = _ref_px
                    else:
                        if abs(_bid_max_level_price-_ref_px) < abs(_ask_min_level_price-_ref_px):
                            price = _bid_max_level_price
                        else:
                            price = _ask_min_level_price

                if bid_Qty == 0:
                    if ask_Qty != 0:
                        price = _ask_min_level_price
                    # locate next lower bid level
                    _bid_max_level_qty = 0
                    self._export_level_access(f'LEVEL_ACCESS BID locate_lower {_bid_max_level_price} //callSnap:next side level')
                    for p, l in sorted(self.bid_level_tree.items(),key=lambda x:x[0], reverse=True):    #从大到小遍历
                        if p<_bid_max_level_price:
                            # if price<=p:
                            #     price = p+1
                            _bid_max_level_price = p
                            _bid_max_level_qty = l.qty
                            break

                if ask_Qty == 0:
                    if bid_Qty != 0:
                        price = _bid_max_level_price
                    # locate next higher ask level
                    _ask_min_level_qty = 0
                    self._export_level_access(f'LEVEL_ACCESS ASK locate_higher {_ask_min_level_price} //callSnap:next side level')
                    for p, l in sorted(self.ask_level_tree.items(),key=lambda x:x[0], reverse=False):    #从小到大遍历
                        if p>_ask_min_level_price:
                            # if price>=p:
                            #     price = p-1
                            _ask_min_level_price = p
                            _ask_min_level_qty = l.qty
                            break

            else:   #后续买卖双方至少一方无委托，或价格无交叉
                if ask_Qty==0 and bid_Qty==0:   # 双方恰好成交，根据下一档价格，可能需要修正成交价
                    if _ask_min_level_qty and price>=_ask_min_level_price: #成交价高于卖方下一档，必须修正到小于等于卖方下一档
                        if _bid_max_level_qty==0 or _bid_max_level_price+1<_ask_min_level_price:    # 买方下一档+1分钱 小于 卖方下一档，修到卖方下一档-1
                            price = _ask_min_level_price-1
                        else:
                            if _ask_min_level_qty <= _bid_max_level_qty:   # 卖方双方下一档只差一分钱，选量小的，同量卖方优先
                                price = _ask_min_level_price
                                ask_Qty = _ask_min_level_qty
                            else:
                                price = _bid_max_level_price
                                bid_Qty = _bid_max_level_qty

                    elif _bid_max_level_qty and price<=_bid_max_level_price: #成交价低于买方下一档，必须修正到大于等于买方下一档
                        if _bid_max_level_qty==0 or _ask_min_level_price>_bid_max_level_price+1: # 卖方下一档分钱 大于 买方下一档+1，修到买方下一档+1
                            price = _bid_max_level_price+1
                        else:
                            if _bid_max_level_qty <= _ask_min_level_qty: # 卖方双方下一档只差一分钱，选量小的，同量买方优先
                                price = _bid_max_level_price
                                bid_Qty = _bid_max_level_qty
                            else:
                                price = _ask_min_level_price
                                ask_Qty = _ask_min_level_qty
                   
                break


        ## 集中竞价期间不需要统计成交信息(TotalVolumeTrade & TotalValueTrade)

        # price 小数位数扩展
        price = self._fmtPrice_inter2snap(price)

        # 价格档
        snap_ask_levels = {}
        snap_bid_levels = {}
        if volumeTrade == 0: # 无法撮合时
            if not show_potential:
                for i in range(0, show_level_nb):
                    snap_ask_levels[i] = price_level(0,0)
                    snap_bid_levels[i] = price_level(0,0)
            else:   #无法撮合时，揭示多档
                snap_ask_levels, snap_bid_levels = self._getLevels(show_level_nb)
        else: #可撮合时，揭示2档
            snap_ask_levels[0] = price_level(price, volumeTrade)
            snap_ask_levels[1] = price_level(0, ask_Qty)
            for i in range(2, show_level_nb):
                snap_ask_levels[i] = price_level(0,0)

            snap_bid_levels[0] = price_level(price, volumeTrade)
            snap_bid_levels[1] = price_level(0, bid_Qty)
            for i in range(2, show_level_nb):
                snap_bid_levels[i] = price_level(0,0)


        #### 开始构造快照
        if self.SecurityIDSource==SecurityIDSource_SZSE:
            if self.instrument_type==INSTRUMENT_TYPE.STOCK or self.instrument_type==INSTRUMENT_TYPE.KZZ:
                snap_call = axsbe_snap_stock(SecurityIDSource=self.SecurityIDSource, source=f"AXOB-call")
            else:
                raise Exception(f'genCallSnap for instrument_type={self.instrument_type} is not ready!')
        elif self.SecurityIDSource==SecurityIDSource_SSE:
            if self.instrument_type==INSTRUMENT_TYPE.BOND or self.instrument_type==INSTRUMENT_TYPE.KZZ or self.instrument_type==INSTRUMENT_TYPE.NHG:
                snap_call = axsbe_snap_stock(SecurityIDSource=self.SecurityIDSource, MsgType=MsgType_exe_sse_bond, source=f"AXOB-call")
            else:
                raise Exception(f'genCallSnap for instrument_type={self.instrument_type} is not ready!')
        
        self._setSnapFixParam(snap_call)

        ## 本地维护参数
        snap_call.ask = snap_ask_levels
        snap_call.bid = snap_bid_levels
		# 以下参数开盘集合竞价期间为0，收盘集合竞价期间有值
        snap_call.NumTrades = self.NumTrades
        snap_call.TotalVolumeTrade = self.TotalVolumeTrade
        snap_call.TotalValueTrade = self.TotalValueTrade
        snap_call.LastPx = self._fmtPrice_inter2snap(self.LastPx)
        snap_call.HighPx = self._fmtPrice_inter2snap(self.HighPx)
        snap_call.LowPx = self._fmtPrice_inter2snap(self.LowPx)
        snap_call.OpenPx = self._fmtPrice_inter2snap(self.OpenPx)
        

        # 本地维护参数
        if self.SecurityIDSource==SecurityIDSource_SZSE:
            snap_call.BidWeightPx = 0   #开盘撮合时期为0
            snap_call.BidWeightSize = 0
            snap_call.AskWeightPx = 0
            snap_call.AskWeightSize = 0
        elif self.SecurityIDSource==SecurityIDSource_SSE:
            if self.BidWeightSize != 0:
                snap_call.BidWeightPx = (int((self.BidWeightValue<<1) / self.BidWeightSize) + 1) >> 1 # 四舍五入
                snap_call.BidWeightPx = self._fmtPrice_inter2snap(snap_call.BidWeightPx)
            else:
                snap_call.BidWeightPx = 0
            snap_call.BidWeightSize = self.BidWeightSize
            
            if self.AskWeightSize != 0:
                snap_call.AskWeightPx = (int((self.AskWeightValue<<1) / self.AskWeightSize) + 1) >> 1 # 四舍五入
                snap_call.AskWeightPx = self._fmtPrice_inter2snap(snap_call.AskWeightPx)
            else:
                snap_call.AskWeightPx = 0
            snap_call.AskWeightSize = self.AskWeightSize

        #最新的一个逐笔消息时戳
        self._setSnapTimestamp(snap_call)

        snap_call.update_TradingPhaseCode(self.TradingPhaseMarket, axsbe_base.TPI.Normal)

        return snap_call
        

    def genTradingSnap(self, isVolatilityBreaking=False, level_nb=10):
        '''
        生成连续竞价期间快照
        level_nb: 快照单边档数
        '''
        snap_bid_levels = {}
        lv = 0
        if not isVolatilityBreaking: #临停期间，各档均填0；非临停期间才从价格档中取值
            self._export_level_access(f'LEVEL_ACCESS BID locate_lower {self.bid_max_level_price} x{level_nb} //tradingSnap:traverse side level')
            for p, l in sorted(self.bid_level_tree.items(),key=lambda x:x[0], reverse=True):    #从大到小遍历
                if self.bid_cage_upper_ex_min_level_qty==0 or p<self.bid_cage_upper_ex_min_level_price:
                    snap_bid_levels[lv] = price_level(self._fmtPrice_inter2snap(p), l.qty)
                    lv += 1
                    if lv>=level_nb:
                        break
        for i in range(lv, level_nb):
            snap_bid_levels[i] = price_level(0, 0)
            
        snap_ask_levels = {}
        lv = 0
        if not isVolatilityBreaking: #临停期间，各档均填0；非临停期间才从价格档中取值
            self._export_level_access(f'LEVEL_ACCESS ASK locate_higher {self.ask_min_level_price} x{level_nb} //tradingSnap:traverse side level')
            for p, l in sorted(self.ask_level_tree.items(),key=lambda x:x[0], reverse=False):    #从小到大遍历
                if self.ask_cage_lower_ex_max_level_qty==0 or p>self.ask_cage_lower_ex_max_level_price:
                    snap_ask_levels[lv] = price_level(self._fmtPrice_inter2snap(p), l.qty)
                    lv += 1
                    if lv>=level_nb:
                        break
        for i in range(lv, level_nb):
            snap_ask_levels[i] = price_level(0, 0)


        if self.instrument_type==INSTRUMENT_TYPE.STOCK or self.instrument_type==INSTRUMENT_TYPE.KZZ:
            snap = axsbe_snap_stock(SecurityIDSource=self.SecurityIDSource, source=f"AXOB-{level_nb}")
        else:
            self.WARN(f'genTradingSnap for instrument_type={self.instrument_type} is not ready!')
            return None # TODO: not ready [Mid priority]
        snap.ask = snap_ask_levels
        snap.bid = snap_bid_levels
        
        # 固定参数
        self._setSnapFixParam(snap)


        # 本地维护参数
        snap.NumTrades = self.NumTrades
        snap.TotalVolumeTrade = self.TotalVolumeTrade
        snap.TotalValueTrade = self.TotalValueTrade
        snap.LastPx = self._fmtPrice_inter2snap(self.LastPx)
        snap.HighPx = self._fmtPrice_inter2snap(self.HighPx)
        snap.LowPx = self._fmtPrice_inter2snap(self.LowPx)
        snap.OpenPx = self._fmtPrice_inter2snap(self.OpenPx)
        

        #维护参数
        if isVolatilityBreaking: #临停期间填0
            snap.BidWeightPx = 0
            snap.BidWeightSize = 0
            snap.AskWeightPx = 0
            snap.AskWeightSize = 0
        else:
            if self.BidWeightSize != 0:
                snap.BidWeightPx = (int((self.BidWeightValue<<1) / self.BidWeightSize) + 1) >> 1 # 四舍五入
                snap.BidWeightPx = self._fmtPrice_inter2snap(snap.BidWeightPx)
            else:
                snap.BidWeightPx = 0
            snap.BidWeightSize = self.BidWeightSize
            
            if self.AskWeightSize != 0:
                snap.AskWeightPx = (int((self.AskWeightValue<<1) / self.AskWeightSize) + 1) >> 1 # 四舍五入
                snap.AskWeightPx = self._fmtPrice_inter2snap(snap.AskWeightPx)
            else:
                snap.AskWeightPx = 0
            snap.AskWeightSize = self.AskWeightSize

        #最新的一个逐笔消息时戳
        self._setSnapTimestamp(snap)

        snap.update_TradingPhaseCode(self.TradingPhaseMarket, axsbe_base.TPI.Normal)

        return snap

    def _clipInt32(self, x):
        if x>(0x7fffffff):
            return 0x7fffffff
        else:
            return x

    def _clipUint32(self, x):
        if x>(0xffffffff):
            return 0xffffffff
        else:
            return x

    
    def _fmtPrice_inter2snap(self, price):
        # price 小数位数扩展
        if self.SecurityIDSource==SecurityIDSource_SZSE:
            # 深圳快照价格精度6位小数（唯有PrevClosePx是4位小数）
            if self.instrument_type==INSTRUMENT_TYPE.STOCK:
                price *= msg_util.PRICE_SZSE_SNAP_PRECISION // PRICE_INTER_STOCK_PRECISION    # 内部2位，输出6位
            elif self.instrument_type==INSTRUMENT_TYPE.FUND:
                price *= msg_util.PRICE_SZSE_SNAP_PRECISION // PRICE_INTER_FUND_PRECISION    # 内部3位，输出6位
            elif self.instrument_type==INSTRUMENT_TYPE.KZZ:
                price *= msg_util.PRICE_SZSE_SNAP_PRECISION // PRICE_INTER_KZZ_PRECISION    # 内部3位，输出6位
            else:
                price = None
        elif self.SecurityIDSource==SecurityIDSource_SSE:
            # 上海快照价格精度3位小数
            if self.instrument_type==INSTRUMENT_TYPE.STOCK:
                price *= msg_util.PRICE_SSE_PRECISION // PRICE_INTER_STOCK_PRECISION    # 内部2位，输出3位
            elif self.instrument_type==INSTRUMENT_TYPE.FUND:
                price *= msg_util.PRICE_SSE_PRECISION // PRICE_INTER_FUND_PRECISION    # 内部3位，输出3位
            else:
                price = None
        else:
            price = None
        return price

    def _getLevels(self, level_nb):
        '''
        输出：卖方最优n档, 买方最优n档
        '''
        snap_ask_levels = {}
        snap_bid_levels = {}
        
        _bid_max_level_price = self.bid_max_level_price
        _bid_max_level_qty = self.bid_max_level_qty
        _ask_min_level_price = self.ask_min_level_price
        _ask_min_level_qty = self.ask_min_level_qty

        for nb in range(level_nb):
            if _ask_min_level_qty!=0:
                snap_ask_levels[nb] = price_level(self._fmtPrice_inter2snap(_ask_min_level_price), _ask_min_level_qty)
                # locate next higher ask level
                _ask_min_level_qty = 0
                self._export_level_access(f'LEVEL_ACCESS ASK locate_higher {_ask_min_level_price} //snap:traverse side level')
                for p, l in sorted(self.ask_level_tree.items(),key=lambda x:x[0], reverse=False):    #从小到大遍历
                    if p>_ask_min_level_price:
                        _ask_min_level_price = p
                        _ask_min_level_qty = l.qty
                        break
            else:
                snap_ask_levels[nb] = price_level(0,0)

            if _bid_max_level_qty!=0:
                snap_bid_levels[nb] = price_level(self._fmtPrice_inter2snap(_bid_max_level_price), _bid_max_level_qty)
                # locate next lower bid level
                _bid_max_level_qty = 0
                self._export_level_access(f'LEVEL_ACCESS BID locate_lower {_bid_max_level_price} //snap:traverse side level')
                for p, l in sorted(self.bid_level_tree.items(),key=lambda x:x[0], reverse=True):    #从大到小遍历
                    if p<_bid_max_level_price:
                        _bid_max_level_price = p
                        _bid_max_level_qty = l.qty
                        break
            else:
                snap_bid_levels[nb] = price_level(0,0)

        return snap_ask_levels, snap_bid_levels

    def _chkSnapTimestamp(self, se_snap, ax_snap):
        '''
        return True: 双方时戳合法
        检查交易所快照和本地重建快照的时戳是否符合：
        深交所本地时戳的秒应小于等交易所快照时戳
        '''

        # 休市阶段，忽略时戳检查
        if se_snap.TradingPhaseMarket==ax_snap.TradingPhaseMarket and \
            (se_snap.TradingPhaseMarket==axsbe_base.TPM.PreTradingBreaking or \
             se_snap.TradingPhaseMarket==axsbe_base.TPM.Breaking or \
             se_snap.TradingPhaseMarket>=axsbe_base.TPM.Ending \
            ):
            return True

        se_timestamp = se_snap.TransactTime
        ax_timestamp = ax_snap.TransactTime

        if self.SecurityIDSource==SecurityIDSource_SZSE:
            return ax_timestamp//1000 <= se_timestamp//1000 +1
        elif self.SecurityIDSource==SecurityIDSource_SSE:
            '''TODO-SSE'''
        else:
            return False

    def are_you_ok(self):
        im_ok = True
        if len(self.market_snaps):
            self.ERR(f'unmatched market snap size={len(self.market_snaps)}:')
            n = 0
            for s,ls in self.market_snaps.items():
                self.ERR(f'\tNumTrades={s}')
                for ss in ls:
                    self.ERR(f'\t\t#{ss._seq}\t@{ss.TransactTime}')
                n += 1
                if n>=3:
                    self.ERR("\t......")
                    break
            im_ok = False
        return im_ok

    @property
    def order_map_size(self):
        return len(self.order_map)

    @property
    def level_tree_size(self):
        return len(self.bid_level_tree) + len(self.ask_level_tree)

    @property
    def bid_level_tree_size(self):
        return len(self.bid_level_tree)

    @property
    def ask_level_tree_size(self):
        return len(self.ask_level_tree)

    def profile(self):
        if self.order_map_size>self.pf_order_map_maxSize: self.pf_order_map_maxSize = self.order_map_size
        if self.level_tree_size>self.pf_level_tree_maxSize: self.pf_level_tree_maxSize = self.level_tree_size
        if self.bid_level_tree_size>self.pf_bid_level_tree_maxSize: self.pf_bid_level_tree_maxSize = self.bid_level_tree_size
        if self.ask_level_tree_size>self.pf_ask_level_tree_maxSize: self.pf_ask_level_tree_maxSize = self.ask_level_tree_size
        if self.AskWeightSize>self.pf_AskWeightSize_max: self.pf_AskWeightSize_max = self.AskWeightSize
        if self.AskWeightValue>self.pf_AskWeightValue_max: self.pf_AskWeightValue_max = self.AskWeightValue
        if self.BidWeightSize>self.pf_BidWeightSize_max: self.pf_BidWeightSize_max = self.BidWeightSize
        if self.BidWeightValue>self.pf_BidWeightValue_max: self.pf_BidWeightValue_max = self.BidWeightValue

    def _describe_px(self, p):
        s = ''
        if p==self.bid_max_level_price:
            s += '\tbid_max'
        if p==self.ask_min_level_price:
            s += '\task_min'
        if p==self.ask_cage_ref_px:
            s += '\task_cage_ref'
        if p==self.bid_cage_ref_px:
            s += '\tbid_cage_ref'
        if self.ask_cage_lower_ex_max_level_qty and p==self.ask_cage_lower_ex_max_level_price:
            s += '\task_cage_lower_ex_max'
        if self.bid_cage_upper_ex_min_level_qty and p==self.bid_cage_upper_ex_min_level_price:
            s += '\tbid_cage_upper_ex_min'
        return s

    def _print_levels(self):
        for p, l in sorted(self.ask_level_tree.items(),key=lambda x:x[0], reverse=True):    #从大到小遍历
            s = f'ask\t{l}{self._describe_px(l.price)}'
            self.DBG(s)
        for p, l in sorted(self.bid_level_tree.items(),key=lambda x:x[0], reverse=True):    #从大到小遍历
            s = f'bid\t{l}{self._describe_px(l.price)}'
            self.DBG(s)

    def _export_level_access(self, msg):
        if EXPORT_LEVEL_ACCESS:
            self.DBG(msg)

    def __str__(self) -> str:
        s = f'axob-behave {self.SecurityID:06d} {self.YYMMDD}-{self.current_inc_tick} msg_nb={self.msg_nb}\n'
        s+= f'  order_map={len(self.order_map)} bid_level_tree={len(self.bid_level_tree)} ask_level_tree={len(self.ask_level_tree)}\n'
        s+= f'  bid_max_level_price={self.bid_max_level_price} bid_max_level_qty={self.bid_max_level_qty}\n'
        s+= f'  ask_min_level_price={self.ask_min_level_price} ask_min_level_qty={self.ask_min_level_qty}\n'
        s+= f'  rebuilt_snaps={len(self.rebuilt_snaps)} market_snaps={len(self.market_snaps)}\n'
        s+= '\n'
        s+= f'  pf_order_map_maxSize={self.pf_order_map_maxSize}({bitSizeOf(self.pf_order_map_maxSize)}b)\n'
        s+= f'  pf_level_tree_maxSize={self.pf_level_tree_maxSize}({bitSizeOf(self.pf_level_tree_maxSize)}b)\n'
        s+= f'  pf_bid_level_tree_maxSize={self.pf_bid_level_tree_maxSize}({bitSizeOf(self.pf_bid_level_tree_maxSize)}b) pf_ask_level_tree_maxSize={self.pf_ask_level_tree_maxSize}({bitSizeOf(self.pf_ask_level_tree_maxSize)}b)\n'
        s+= f'  pf_AskWeightSize_max={self.pf_AskWeightSize_max}({bitSizeOf(self.pf_AskWeightSize_max)}b)\n'
        s+= f'  pf_AskWeightValue_max={self.pf_AskWeightValue_max}({bitSizeOf(self.pf_AskWeightValue_max)}b)\n'
        s+= f'  pf_BidWeightSize_max={self.pf_BidWeightSize_max}({bitSizeOf(self.pf_BidWeightSize_max)}b)\n'
        s+= f'  pf_BidWeightValue_max={self.pf_BidWeightValue_max}({bitSizeOf(self.pf_BidWeightValue_max)}b)\n'

        return s

    def save(self):
        '''save/load 用于保存/加载测试时刻'''
        data = {}
        for attr in self.__slots__:
            if attr in ['logger', 'DBG', 'INFO', 'WARN', 'ERR']:
                continue

            value = getattr(self, attr)
            if attr in ['order_map', 'bid_level_tree', 'ask_level_tree']:
                data[attr] = {}
                for i in value:
                    data[attr][i] = value[i].save()
            elif attr == 'rebuilt_snaps' or attr == 'market_snaps':
                data[attr] = {}
                for i in value:
                    data[attr][i] = [x.save() for x in value[i]]
            elif attr == 'last_snap':
                if value is None:
                    data[attr] = None
                else:
                    data[attr] = value.save()
            else:
                data[attr] = value
        return data

    def load(self, data):
        setattr(self, 'instrument_type', data['instrument_type'])
        for attr in self.__slots__:
            if attr in ['logger', 'DBG', 'INFO', 'WARN', 'ERR']:
                continue

            if attr == 'order_map':
                v = {}
                for i in data[attr]:
                    v[i] = ob_order(axsbe_order(), INSTRUMENT_TYPE.UNKNOWN)
                    v[i].load(data[attr][i])
                setattr(self, attr, v)
            elif attr in ['bid_level_tree', 'ask_level_tree']:
                v = {}
                for i in data[attr]:
                    v[i] = level_node(-1, -1, -1)
                    v[i].load(data[attr][i])
                setattr(self, attr, v)
            elif attr == 'rebuilt_snaps' or attr == 'market_snaps':
                v = {}
                for i in data[attr]:
                    vv = []
                    for d in data[attr][i]:
                        if self.instrument_type==INSTRUMENT_TYPE.STOCK:
                            s = axsbe_snap_stock()
                        else:
                            raise f'unable to load instrument_type={self.instrument_type}'
                        s.load(d)
                        vv.append(s)
                    v[i] = vv
                setattr(self, attr, v)
            elif attr == 'last_snap':
                if data[attr] is None:
                    v = None
                else:
                    if self.instrument_type==INSTRUMENT_TYPE.STOCK:
                        v = axsbe_snap_stock()
                    else:
                        raise f'unable to load instrument_type={self.instrument_type}'
                    v.load(data[attr])
                setattr(self, attr, v)
            else:
                setattr(self, attr, data[attr])

            # elif attr in data:
            #     setattr(self, attr, data[attr])
            # else:
            #     print(f'AXOB.{attr} not in load data!')
            #     setattr(self, attr, 0)

        ## 日志
        self.logger = logging.getLogger(f'{self.SecurityID:06d}')
        g_logger = logging.getLogger('main')
        self.logger.setLevel(g_logger.getEffectiveLevel())
        for h in g_logger.handlers:
            self.logger.addHandler(h)
            axob_logger.addHandler(h) #这里补上模块日志的handler，有点ugly TODO: better way [low prioryty]

        self.DBG = self.logger.debug
        self.INFO = self.logger.info
        self.WARN = self.logger.warning
        self.ERR = self.logger.error
