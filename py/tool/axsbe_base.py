# -*- coding: utf-8 -*-

import abc
import numpy as np
from enum import Enum

## 交易所代码
SecurityIDSource_NULL = 0
SecurityIDSource_SSE = 101
SecurityIDSource_SZSE = 102

## 消息类型
MsgType_exe_stock   = 191
MsgType_exe_sse_bond   = 84
MsgTypes_exe = [MsgType_exe_stock, MsgType_exe_sse_bond]


MsgType_order_stock = 192
MsgType_order_sse_bond_add = 65
MsgType_order_sse_bond_del = 68
MsgTypes_order = [MsgType_order_stock, MsgType_order_sse_bond_add, MsgType_order_sse_bond_del]


MsgType_snap_stock      = 111 #深交所股票、可转债；上交所股票基金
MsgType_snap_szse_bond  = 211 #深交所债券现券、逆回购
MsgType_snap_sse_bond   = 38  #上交所债券、可转债、逆回购
MsgTypes_snap  = [MsgType_snap_stock, MsgType_snap_szse_bond, MsgType_snap_sse_bond]


MsgType_heartbeat = 1
MsgType_status_sse_bond = 83
MsgTypes_headerOnly = [MsgType_heartbeat, MsgType_status_sse_bond]
    
class INSTRUMENT_TYPE(Enum): # 3bit
    STOCK  = 0   #股票
    FUND   = 1   #基金
    KZZ    = 2   #可转债
    OPTION = 3   #期权
    BOND   = 4   #债券
    NHG    = 5   #逆回购

    UNKNOWN = -1

# ## TradingPhase 交易阶段代码 Code0
# TP_Starting = 'S'  #启动（开市前）
# TP_OpenCall = 'O'  #开盘集合竞价
# TP_preTradingBreaking = 'p'    #集合竞价与连续竞价之间
# TP_Trading = 'T'   #连续竞价上半场
# TP_Breaking = 'B'  #休市
# TP_CloseCall = 'C' #收盘集合竞价
# TP_Ending = 'E'    #已闭市
# TP_HangingUp = 'H' #临时停牌
# TP_AfterTrading = 'A'  #盘后交易
# TP_VolatilityBreaking = 'V'    #波动性中断
# TradingPhaseMarket_str = {
#     TP_Starting : '启动',
#     TP_OpenCall : '开盘集合竞价',
#     TP_preTradingBreaking : '集合竞价与连续竞价之间',
#     TP_Trading : '连续竞价',
#     TP_Breaking : '休市',
#     TP_CloseCall : '收盘集合竞价',
#     TP_Ending : '已闭市',
#     TP_HangingUp : '临时停牌',
#     TP_AfterTrading : '盘后交易',
#     TP_VolatilityBreaking : '波动性中断',
#     None : '无意义',
# }

# ## TradingPhase 交易阶段代码 Code1
# TP_Normal = 0
# TP_NoTrade = 1
# TradingPhaseSecurity_str = {
#     TP_Normal : '正常',
#     TP_NoTrade : '全天停牌',
#     None : '无意义',
# }

class TPM():
    # TradingPhase of Market，市场交易阶段内部编码
    Starting = 0
    OpenCall = 1
    PreTradingBreaking = 2
    AMTrading = 3
    Breaking = 4
    PMTrading = 5
    CloseCall = 6
    AfterCloseTrading = 7 # 虽然我们编码这个交易阶段，但在111/192/191消息中不会碰到，其用于300611/303711消息
    VolatilityBreaking = 8
    Ending = 9
    HangingUp = 10
    FusingCall = 11
    FusingEnd = 12

    ContinuousAutomaticMatching = 35 #连续自动撮合，上海债券市场状态中没有时戳，所以分不开上下午
    Closing = 24 #闭市，上海债券市场状态中在15:00:00之后
    OffMarket = 99     #未上市，上海债券市场状态独有

    Unknown = -1

    TPM_str = {
        Starting : '启动',
        OpenCall : '开盘集合竞价',
        PreTradingBreaking : '开盘集合竞价后休市',
        AMTrading : '连续竞价[上午]',
        Breaking : '中午休市',
        PMTrading : '连续竞价[下午]',
        CloseCall : '收盘集合竞价',
        AfterCloseTrading : '盘后交易',
        Ending : '已闭市',
        VolatilityBreaking : '波动性中断',
        HangingUp : '停牌',
        FusingCall : '熔断时段（盘中集合竞价）',
        FusingEnd : '熔断时段（暂停交易至闭市）',
        ContinuousAutomaticMatching: '连续自动撮合',
        Closing: '闭市',
        OffMarket: '未上市',
        Unknown : '未知',
    }

    def str(tpm):
        return TPM.TPM_str[tpm]

class TPI():
    # TradingPhase of Instrument，标的交易状态内部编码
    Normal = 0   #深圳/上海的原始值中，Normal和NoTrade是互相颠倒的；这里是深圳的值；上海1=Normal,0=NoTrade。
    NoTrade = 1

    Unknown = 3

    TPI_str = {
        Normal : '正常交易',
        NoTrade : '不可交易',
        Unknown : '未知',
    }
    def str(tpi):
        return TPI.TPI_str[tpi]

class TPC2():
    # TradingPhase of Code[2] (上海L2股票/基金/债券), 4b
    OffMarket = 0
    OnMarket = 1

    Unknown = 15

    TPC2_str = {
        OffMarket : '未上市',
        OnMarket : '已上市',
        Unknown : '未知',
    }
    def str(tpc2):
        return TPC2.TPC2_str[tpc2]
class TPC3():
    # TradingPhase of Code[3] (上海L2), 2b
    RejectOrder = 0
    AcceptOrder = 1

    Unknown = 3

    TPC3_str = {
        RejectOrder : '不接受订单申报',
        AcceptOrder : '可接受订单申报',
        Unknown : '未知',
    }
    def str(tpc3):
        return TPC3.TPC3_str[tpc3]

class axsbe_base(metaclass=abc.ABCMeta):
    '''
    sbe消息基类：
    目前先按照深交所精度来实现，待需要加入上交所支持时通过SecurityIDSource实现精度切换。
    '''
    def __init__(self, MsgType, SecurityIDSource):
        self.SecurityIDSource = SecurityIDSource #"证券代码源101=上交所;102=深交所;103=香港交易所"
        self.MsgType = MsgType
        self.SecurityID = -1
        self.ChannelNo = 0xffff
        self.ApplSeqNum = 0xffffffffffffffff
        self.TransactTime = 0   #上海snap时戳精度秒、逐笔精度10毫秒，在构造时统一到毫秒？

        self._tick = None
        self._HHMMSSms = None
        self._ms = None


    @property
    def ms(self):
        '''日内时间戳，精度ms，可用于计算'''
        if self._ms is None:
            self.tick
        return self._ms

    @property
    def HHMMSSms(self):
        '''日内时间戳，可比较; ms只有3位'''
        if self._HHMMSSms is None:
            if self.SecurityIDSource == SecurityIDSource_SZSE:
                self._HHMMSSms = self.TransactTime % 1000000000
            elif self.SecurityIDSource == SecurityIDSource_SSE:
                if self.MsgType==MsgType_order_stock or self.MsgType==MsgType_exe_stock: #精度10ms
                    self._HHMMSSms = self.TransactTime * 10
                elif self.MsgType==MsgType_snap_stock: #精度秒
                    self._HHMMSSms = self.TransactTime * 1000
                elif self.MsgType==MsgType_snap_sse_bond or self.MsgType==MsgType_order_sse_bond_del or self.MsgType==MsgType_order_sse_bond_add or self.MsgType==MsgType_exe_sse_bond: #精度ms
                    self._HHMMSSms = self.TransactTime
                elif self.MsgType==MsgType_heartbeat or self.MsgType==MsgType_status_sse_bond:
                    self._HHMMSSms = 0 #没有时戳
                else:
                    raise Exception(f'Not support SSE MsgType={self.MsgType}')
            else:
                raise Exception(f'Not support SecurityIDSource={self.SecurityIDSource}')
        return self._HHMMSSms

    @property
    def tick(self):
        '''日内时间戳，可阅读'''
        if self._tick is None:
            t = self.HHMMSSms
            ms = t % 1000
            ss = (t % (100000))   // 1000
            mm = (t % (10000000)) // 100000
            hh = (t )             // 10000000
            self._tick = f"{hh:02d}:{mm:02d}:{ss:02d}.{ms:03d}"
            self._ms = ms + ss * 1000 + mm * 60 * 1000 + hh * 3600 * 1000
        return self._tick
        
    def is_opened(self):
        '''已过开盘集合竞价'''
        if self.HHMMSSms < 92500000:
            return False
        else:
            return True

            
    @property
    def TradingPhaseMarket(self):
        '''
        市场交易阶段：
        对于快照行情，其内部历史数据自带市场交易阶段字段，将重载本接口。
        对于逐笔委托和逐笔成交，利用时戳判断交易阶段。
        '''
        t = self.HHMMSSms
        if t is None:
            return TPM.Starting
        if t < 91500000:
            return TPM.Starting
        elif t < 92500000: #逐笔委托的时戳不会等于925；只有逐笔成交会；而925的逐笔成交代表着开盘集合竞价结束。收盘集合竞价同理。
            return TPM.OpenCall
        elif t < 93000000:
            return TPM.PreTradingBreaking
        elif t < 113000000:
            return TPM.AMTrading
        elif t < 130000000:
            return TPM.Breaking
        elif t < 145700000:
            return TPM.PMTrading
        elif t < 150000000:
            return TPM.CloseCall
        else:
            return TPM.Ending

    @property
    def TradingPhase_str(self):
        return TPM.str(self.TradingPhaseMarket)

    @property
    @abc.abstractmethod
    def bytes_stream(self)->bytes(): 
        '''将字段打包成字节流，派生类需重载'''
        return bytes()
        
    @property
    def bytes_str(self):
        '''打印字节流'''
        bin = self.bytes_stream
        l = ["%02X"%i for i in bin]
        return " ".join(l)

    @property
    def bytes_np(self):
        '''将字段打包成numpy字节流'''
        bin = self.bytes_stream
        np_array = np.frombuffer(bin, dtype=np.uint8)
        # print(np_array.shape)
        # print(np_array)
        return np_array

    @abc.abstractmethod
    def unpack_stream(self, bytes_i:bytes):
        return NotImplemented

    def unpack_np(self, np_i:np.ndarray):
        '''将numpy字节流解包成字段值'''
        bytes_i = np_i.tobytes()
        self.unpack_stream(bytes_i)

        # 清除内部缓存
        self._tick = None
        self._HHMMSSms = None
        self._ms = None
        
    @property
    @abc.abstractmethod
    def ccode(self)->str:
        '''打印与hls c相同格式的日志'''
        return NotImplemented


    @abc.abstractmethod
    def save(self):
        return NotImplemented

    @abc.abstractmethod
    def load(self, data):
        return NotImplemented
