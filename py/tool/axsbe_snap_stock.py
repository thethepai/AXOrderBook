# -*- coding: utf-8 -*-

import tool.axsbe_base as axsbe_base
from tool.axsbe_base import TPM, TPI, TPC2, TPC3
import struct

class price_level:
    '''价格档位'''
    __slots__ = [
        'Price', 
        'Qty',

        '_OrderQue'  # 排队订单，历史数据中未保留此字段;重建订单簿时可以构造此字段，每个元素必须含有 OrderQty 和 ApplSeqNum 两字段
        ]

    def __init__(self, Price, Qty):
        self.Price = Price  # 6位小数
        self.Qty = Qty
        self._OrderQue = []

    # def addQ(self, orderList, max_n):
    #     assert orderList.head is not None
        
    #     n = 0
    #     l = orderList.head
    #     while l is not None:
    #         if n >= max_n:
    #             break
    #         self._OrderQue.append(l.orderNode)
    #         n += 1
    #         l = l.next

    def __eq__(self,other):
        return self.Price == other.Price and self.Qty == other.Qty #价格和数量相等就认为相等，不需要比对排队订单
    
    def __str__(self):
        '''打印log'''
        s = f"{self.Price} * {self.Qty}"
        sq = [f"{orderNode.OrderQty}({orderNode.ApplSeqNum})" for orderNode in self._OrderQue]
        if len(sq): #排队订单为空时，表示未重建排队订单，不需要打印
            s += "\t["+" ".join(sq)+"]"
        return s
        
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


class axsbe_snap_stock(axsbe_base.axsbe_base):
    __slots__ = [
        'SecurityIDSource',
        'MsgType',
        'SecurityID',
        'ChannelNo',
        'TransactTime',         #SH-STOCK&BOND.DataTimeStamp(32b), SZ:64b
                                #SH-STOCK: 143025   表示14:30:25
                                #SH-BOND:  143025002表示14:30:25.002
                                #SZ: YYYYMMDDHHMMSSsss(毫秒)
        'TradingPhaseCode',
        'NumTrades',            #SH:32b SZ:64b
        'TotalVolumeTrade',
        'TotalValueTrade',
        'PrevClosePx',
        'LastPx',
        'OpenPx',
        'HighPx',
        'LowPx',
        'BidWeightPx',              #SH-BOND.AltWeightedAvgBidPx
        'BidWeightSize',            #SH-BOND.TotalBidQty
        'AskWeightPx',              #SH-BOND.AltWeightedAvgOfferPx
        'AskWeightSize',            #SH-BOND.TotalOfferQty
        'UpLimitPx',                #SZ
        'DnLimitPx',                #SZ
        'bid',
        'ask',

        'TradingPhaseCodePack',     #SH-STOCK

        # for debug
        'AskWeightPx_uncertain', #加权价无法确定
        '_seq',
        '_source',  # MD=from MarketData; AXOB=AXOrderBook rebuild

    ]

    def __init__(self, SecurityIDSource=axsbe_base.SecurityIDSource_NULL, source="MD", MsgType=axsbe_base.MsgType_snap_stock):
        super(axsbe_snap_stock, self).__init__(MsgType, SecurityIDSource)
        self.TradingPhaseCode = 0
        self.NumTrades = 0
        self.TotalVolumeTrade = 0
        self.TotalValueTrade = 0
        self.PrevClosePx = 0
        self.LastPx = 0
        self.OpenPx = 0
        self.HighPx = 0
        self.LowPx = 0
        self.BidWeightPx = 0
        self.BidWeightSize = 0
        self.AskWeightPx = 0
        self.AskWeightSize = 0
        self.UpLimitPx = 0
        self.DnLimitPx = 0

        self.bid = dict(zip(range(0, 10), [price_level(0, 0)] * 10))
        self.ask = dict(zip(range(0, 10), [price_level(0, 0)] * 10))

        self.TradingPhaseCodePack = 0

        self.AskWeightPx_uncertain = False

        self._seq = -1
        self._source = source


    def load_dict(self, dict:dict):
        '''从字典加载字段'''
        #公共头
        self.SecurityIDSource = dict['SecurityIDSource']
        self.MsgType = dict['MsgType']
        self.SecurityID = dict['SecurityID']
        self.ChannelNo = dict['ChannelNo']

        if self.SecurityIDSource == axsbe_base.SecurityIDSource_SZSE:
            self.TradingPhaseCode = dict['TradingPhase']
            self.NumTrades = dict['NumTrades']
            self.TotalVolumeTrade = dict['TotalVolumeTrade']
            self.TotalValueTrade = dict['TotalValueTrade']
            self.PrevClosePx = dict['PrevClosePx']
            self.LastPx = dict['LastPx']
            self.OpenPx = dict['OpenPx']
            self.HighPx = dict['HighPx']
            self.LowPx = dict['LowPx']
            self.BidWeightPx = dict['BidWeightPx']
            self.BidWeightSize = dict['BidWeightSize']
            self.AskWeightPx = dict['AskWeightPx']
            self.AskWeightSize = dict['AskWeightSize']
            self.UpLimitPx = dict['UpLimitPx']
            self.DnLimitPx = dict['DnLimitPx']
            self.TransactTime = dict['TransactTime']

            for i in range(10):
                self.bid[i] = price_level(dict['BidLevel[%d].Price'%i], dict['BidLevel[%d].Qty'%i])
                self.ask[i] = price_level(dict['AskLevel[%d].Price'%i], dict['AskLevel[%d].Qty'%i])
        elif self.SecurityIDSource == axsbe_base.SecurityIDSource_SSE:
            self.TradingPhaseCode = dict['TradingPhase']
            self.NumTrades = dict['NumTrades']
            self.TotalVolumeTrade = dict['TotalVolumeTrade']
            self.TotalValueTrade = dict['TotalValueTrade']
            if self.MsgType==axsbe_base.MsgType_snap_stock:
                self.PrevClosePx = dict['PrevClosePx']
                self.TradingPhaseCodePack = dict['TradingPhaseCodePack']
                self.BidWeightPx = dict['BidWeightPx']
                self.BidWeightSize = dict['BidWeightSize']
                self.AskWeightPx = dict['AskWeightPx']
                self.AskWeightSize = dict['AskWeightSize']
            else:
                self.BidWeightPx = dict['AltWeightedAvgBidPx']
                self.BidWeightSize = dict['TotalBidQty']
                self.AskWeightPx = dict['AltWeightedAvgOfferPx']
                self.AskWeightSize = dict['TotalOfferQty']
            self.LastPx = dict['LastPx']
            self.OpenPx = dict['OpenPx']
            self.HighPx = dict['HighPx']
            self.LowPx = dict['LowPx']
            self.TransactTime = dict['DataTimeStamp']

            for i in range(10):
                self.bid[i] = price_level(dict['BidLevel[%d].Price'%i], dict['BidLevel[%d].Qty'%i])
                self.ask[i] = price_level(dict['AskLevel[%d].Price'%i], dict['AskLevel[%d].Qty'%i])

        else:
            raise Exception(f'Not support SecurityIDSource={self.SecurityIDSource}')

    def is_same(self, another):
        if not isinstance(another, axsbe_snap_stock):
            return False
        '''用于比较模拟撮合和历史数据是否一致'''
        MsgType_isSame = self.MsgType == another.MsgType
        SecurityIDSource_isSame = self.SecurityIDSource == another.SecurityIDSource
        ChannelNo_isSame = self.ChannelNo == another.ChannelNo
        TradingPhaseCode_isSame = self.TradingPhaseCode == another.TradingPhaseCode   ## AXOB能构造出TPCode吗
        SecurityID_isSame = self.SecurityID == another.SecurityID
        NumTrades_isSame = self.NumTrades == another.NumTrades
        TotalVolumeTrade_isSame = self.TotalVolumeTrade == another.TotalVolumeTrade
        TotalValueTrade_isSame = self.TotalValueTrade == another.TotalValueTrade
        PrevClosePx_isSame = self.PrevClosePx == another.PrevClosePx
        LastPx_isSame = self.LastPx == another.LastPx
        OpenPx_isSame = self.OpenPx == another.OpenPx
        HighPx_isSame = self.HighPx == another.HighPx
        LowPx_isSame = self.LowPx == another.LowPx
        BidWeightPx_isSame = self.BidWeightPx == another.BidWeightPx
        BidWeightSize_isSame = self.BidWeightSize == another.BidWeightSize
        AskWeightPx_isSame = self.AskWeightPx == another.AskWeightPx if not self.AskWeightPx_uncertain and not another.AskWeightPx_uncertain else True  #任意一方加权价无法确定，则跳过
        AskWeightSize_isSame = self.AskWeightSize == another.AskWeightSize
        UpLimitPx_isSame = self.UpLimitPx == another.UpLimitPx
        DnLimitPx_isSame = self.DnLimitPx == another.DnLimitPx
        bid_isSame = True
        for i in range(10):
            if self.bid[i] != another.bid[i]:
                bid_isSame = False
                
        ask_isSame = True
        for i in range(10):
            if self.ask[i] != another.ask[i]:
                ask_isSame = False

        # TransactTime_isSame = self.TransactTime == another.TransactTime   ## 不关心时戳是否一致

        if  MsgType_isSame \
            and SecurityIDSource_isSame \
            and ChannelNo_isSame \
            and TradingPhaseCode_isSame \
            and PrevClosePx_isSame \
            and SecurityID_isSame \
            and NumTrades_isSame \
            and TotalVolumeTrade_isSame \
            and TotalValueTrade_isSame \
            and LastPx_isSame \
            and OpenPx_isSame \
            and HighPx_isSame \
            and LowPx_isSame \
            and BidWeightPx_isSame \
            and BidWeightSize_isSame \
            and AskWeightPx_isSame \
            and AskWeightSize_isSame \
            and UpLimitPx_isSame \
            and DnLimitPx_isSame \
            and bid_isSame \
            and ask_isSame :
            return True
        return False

    def is_like(self, another):
        '''10档一致，时戳接近；加权价格不一定一致，用于有丢包时比较'''
        MsgType_isSame = self.MsgType == another.MsgType
        SecurityIDSource_isSame = self.SecurityIDSource == another.SecurityIDSource
        ChannelNo_isSame = self.ChannelNo == another.ChannelNo
        # TradingPhaseCode_isSame = self.TradingPhaseCode == another.TradingPhaseCode
        SecurityID_isSame = self.SecurityID == another.SecurityID
        NumTrades_isSame = self.NumTrades == another.NumTrades
        TotalVolumeTrade_isSame = self.TotalVolumeTrade == another.TotalVolumeTrade
        TotalValueTrade_isSame = self.TotalValueTrade == another.TotalValueTrade
        PrevClosePx_isSame = self.PrevClosePx == another.PrevClosePx
        LastPx_isSame = self.LastPx == another.LastPx
        OpenPx_isSame = self.OpenPx == another.OpenPx
        HighPx_isSame = self.HighPx == another.HighPx
        LowPx_isSame = self.LowPx == another.LowPx
        BidWeightPx_isSame = self.BidWeightPx == another.BidWeightPx
        BidWeightSize_isSame = self.BidWeightSize == another.BidWeightSize
        AskWeightPx_isSame = self.AskWeightPx == another.AskWeightPx
        AskWeightSize_isSame = self.AskWeightSize == another.AskWeightSize
        UpLimitPx_isSame = self.UpLimitPx == another.UpLimitPx
        DnLimitPx_isSame = self.DnLimitPx == another.DnLimitPx
        bid_isSame = True
        for i in range(10):
            if self.bid[i] != another.bid[i]:
                bid_isSame = False
                
        ask_isSame = True
        for i in range(10):
            if self.ask[i] != another.ask[i]:
                ask_isSame = False

        # TransactTime_isSame = self.TransactTime == another.TransactTime
        ms_isSame = abs(self.ms, another.ms) < 500
            # and BidWeightPx_isSame \
            # and BidWeightSize_isSame \
            # and AskWeightPx_isSame \
            # and AskWeightSize_isSame \
        if  MsgType_isSame \
            and SecurityIDSource_isSame \
            and ChannelNo_isSame \
            and PrevClosePx_isSame \
            and SecurityID_isSame \
            and NumTrades_isSame \
            and TotalVolumeTrade_isSame \
            and TotalValueTrade_isSame \
            and LastPx_isSame \
            and OpenPx_isSame \
            and HighPx_isSame \
            and LowPx_isSame \
            and UpLimitPx_isSame \
            and DnLimitPx_isSame \
            and bid_isSame \
            and ask_isSame \
            and ms_isSame:
            return True
        return False

    @property
    def TradingPhaseMarket(self):
        if self.SecurityIDSource == axsbe_base.SecurityIDSource_SZSE:
            Code0 = self.TradingPhaseCode&0xf
            if Code0==0:
                return TPM.Starting
            elif Code0==1:
                return TPM.OpenCall
            elif Code0==2:
                if self.HHMMSSms < 120000000:
                    return TPM.AMTrading
                else:
                    return TPM.PMTrading
            elif Code0==3:
                if self.HHMMSSms < 93100000:
                    return TPM.PreTradingBreaking
                else:
                    return TPM.Breaking
            elif Code0==4:
                return TPM.CloseCall
            elif Code0==5:
                return TPM.Ending
            elif Code0==6:
                return TPM.HangingUp
            elif Code0==7:
                return TPM.AfterCloseTrading
            elif Code0==8:
                return TPM.VolatilityBreaking
            else:
                raise Exception(f'Unknown SZSE TradingPhaseCode={Code0}')
        elif self.SecurityIDSource == axsbe_base.SecurityIDSource_SSE:
            Code0 = self.TradingPhaseCode
            if Code0==0:
                return TPM.Starting
            elif Code0==1:
                return TPM.OpenCall
            elif Code0==2:  #TODO: Trade阶段，两个休市是否在里面？
                if self.HHMMSSms < 93000000:
                    return TPM.PreTradingBreaking
                elif self.HHMMSSms < 113000000:
                    return TPM.AMTrading
                elif self.HHMMSSms < 130000000:
                    return TPM.Breaking
                else:
                    return TPM.PMTrading
            elif Code0==4:
                return TPM.CloseCall
            elif Code0==5:
                return TPM.Ending
            elif Code0==6:
                return TPM.HangingUp
            elif Code0==9:
                return TPM.FusingCall
            elif Code0==10:
                return TPM.FusingEnd
            elif Code0==11:          #债券only，产品未上市
                return TPM.OffMarket
            elif Code0==12:          #债券only，交易结束
                return TPM.Ending
            else:
                raise Exception(f'Unknown SSE TradingPhaseCode={Code0}')
        else:
            raise Exception(f'Not support SecurityIDSource={self.SecurityIDSource}')

    @property
    def TradingPhaseSecurity(self):
        if self.SecurityIDSource == axsbe_base.SecurityIDSource_SZSE:
            Code1 = self.TradingPhaseCode>>4
            if Code1==0:
                return TPI.Normal
            elif Code1==1:
                return TPI.NoTrade
            else:
                raise Exception(f'Not support SZSE MsgType={self.MsgType}')
        elif self.SecurityIDSource == axsbe_base.SecurityIDSource_SSE:
            if self.MsgType==axsbe_base.MsgType_snap_stock:
                Code1 = self.TradingPhaseCodePack>>6
                Code2 = (self.TradingPhaseCodePack>>2)&0xf
                Code3 = (self.TradingPhaseCodePack)&0x3
                if Code1==1 and Code2==1 and Code3==1: # 可正常交易+已上市+可接受订单申报
                    return TPI.Normal
                elif Code1==0 or Code2==0 or Code3==0:
                    return TPI.NoTrade
                else:
                    raise Exception(f'Unknown TPI of SSE stock/fund Code1={Code1} Code2={Code2} Code3={Code3}')
            elif self.MsgType==axsbe_base.MsgType_snap_sse_bond:
                if self.TradingPhaseCode==11 or self.TradingPhaseCode==6:
                    return TPI.NoTrade
                else:
                    return TPI.Normal
            else:
                raise Exception(f'Not support SSE MsgType={self.MsgType}')
        else:
            raise Exception(f'Not support SecurityIDSource={self.SecurityIDSource}')

    def update_TradingPhaseCode(self, tpm:TPM, tpi:TPI, tpc2:TPC2=TPC2.Unknown, tpc3:TPC3=TPC3.Unknown):
        if self.SecurityIDSource == axsbe_base.SecurityIDSource_SZSE:
            if tpm==TPM.Starting: Code0=0
            elif tpm==TPM.OpenCall: Code0=1
            elif tpm==TPM.AMTrading or tpm==TPM.PMTrading: Code0=2
            elif tpm==TPM.PreTradingBreaking or tpm==TPM.Breaking: Code0=3
            elif tpm==TPM.CloseCall: Code0=4
            elif tpm==TPM.Ending: Code0=5
            elif tpm==TPM.HangingUp: Code0=6
            elif tpm==TPM.AfterCloseTrading: Code0=7
            elif tpm==TPM.VolatilityBreaking: Code0=8
            else:
                Code0 = 0xf
                
            if tpi==TPI.Normal:    Code1 = 0
            elif tpi==TPI.NoTrade: Code1 = 1
            else:                  Code1 = 0xf

            self.TradingPhaseCode = (Code1<<4) + Code0
        elif self.SecurityIDSource == axsbe_base.SecurityIDSource_SSE:
            if tpm==TPM.Starting:   self.TradingPhaseCode = 0
            elif tpm==TPM.OpenCall: self.TradingPhaseCode = 1
            elif tpm==TPM.AMTrading or tpm==TPM.PMTrading: self.TradingPhaseCode = 2
            # elif tpm==TPM.PreTradingBreaking or tpm==TPM.Breaking: self.TradingPhaseCode = 3 #option only
            elif tpm==TPM.CloseCall: self.TradingPhaseCode = 4
            elif tpm==TPM.Ending: self.TradingPhaseCode = 5
            elif tpm==TPM.HangingUp: self.TradingPhaseCode = 6
            # elif tpm==TPM.VolatilityBreaking: self.TradingPhaseCode = 8 # option only
            else:
                self.TradingPhaseCode = 0xff
            
            if self.MsgType==axsbe_base.MsgType_snap_stock:
                # 2bit
                if tpi==TPI.Normal:    Code1 = 1
                elif tpi==TPI.NoTrade: Code1 = 0
                else:                  Code1 = 0x3
                # 4bit
                if tpc2==TPC2.OffMarket:    Code2 = 0
                elif tpc2==TPC2.OnMarket:   Code2 = 1
                else:                       Code2 = 0xf
                # 2bit
                if tpc3==TPC3.RejectOrder:      Code3 = 0
                elif tpc3==TPC3.AcceptOrder:    Code3 = 1
                else:                           Code3 = 0x3

                self.TradingPhaseCodePack = (Code1<<6) + (Code2<<2) + Code3

        else:
            raise Exception(f'Not support SecurityIDSource={self.SecurityIDSource}')

    @property
    def TradingPhase_str(self):
        if self.SecurityIDSource == axsbe_base.SecurityIDSource_SZSE:
            return TPM.str(self.TradingPhaseMarket) + ";" + TPI.str(self.TradingPhaseSecurity)
        elif self.SecurityIDSource == axsbe_base.SecurityIDSource_SSE:
            if self.MsgType==axsbe_base.MsgType_snap_stock:
                Code1 = self.TradingPhaseCodePack>>6
                Code1_map_uniform = 1-Code1 #深圳和上海0/1是颠倒的，TPI是按照深圳定义，这里需要修改
                Code2 = (self.TradingPhaseCodePack>>2)&0xf
                Code3 = (self.TradingPhaseCodePack)&0x3
                return TPM.str(self.TradingPhaseMarket) + ";" + TPI.str(Code1_map_uniform) + ";" + TPC2.str(Code2) + ";" + TPC3.str(Code3)
            elif self.MsgType==axsbe_base.MsgType_snap_sse_bond:
                return TPM.str(self.TradingPhaseMarket)
        else:
            raise Exception(f'Not support SecurityIDSource={self.SecurityIDSource}')

    def __str__(self):
        '''打印log，只有合法的SecurityIDSource才能被打印'''
        if self.SecurityIDSource == axsbe_base.SecurityIDSource_SZSE:
            s = f'''{self._source}
    {"%06d"%self.SecurityID}
    NumTrades={self.NumTrades}  TVol={self.TotalVolumeTrade}  TVal={self.TotalValueTrade} PrxCls={self.PrevClosePx}
    Px={self.LastPx}  O={self.OpenPx}  H={self.HighPx}  L={self.LowPx}
    UpLimitPx={self.UpLimitPx}  DnLimitPx={self.DnLimitPx}
    BidWeightPx={self.BidWeightPx}  BidWeightSize={self.BidWeightSize}
    AskWeightPx={self.AskWeightPx}  AskWeightSize={self.AskWeightSize}
    Ask[9]={self.ask[9]}
    Ask[8]={self.ask[8]}
    Ask[7]={self.ask[7]}
    Ask[6]={self.ask[6]}
    Ask[5]={self.ask[5]}
    Ask[4]={self.ask[4]}
    Ask[3]={self.ask[3]}
    Ask[2]={self.ask[2]}
    Ask[1]={self.ask[1]}
    Ask[0]={self.ask[0]}
    --
    Bid[0]={self.bid[0]}
    Bid[1]={self.bid[1]}
    Bid[2]={self.bid[2]}
    Bid[3]={self.bid[3]}
    Bid[4]={self.bid[4]}
    Bid[5]={self.bid[5]}
    Bid[6]={self.bid[6]}
    Bid[7]={self.bid[7]}
    Bid[8]={self.bid[8]}
    Bid[9]={self.bid[9]}
    @{self.TransactTime} ({self.TradingPhase_str})
    AskWeightPx_uncertain={self.AskWeightPx_uncertain}
'''
        elif self.SecurityIDSource == axsbe_base.SecurityIDSource_SSE:
            PrxCls = ''
            if self.MsgType==axsbe_base.MsgType_snap_stock:
                PrxCls = f'PrxCls={self.PrevClosePx}'

            s = f'''{self._source}
    {"%06d"%self.SecurityID}
    NumTrades={self.NumTrades}  TVol={self.TotalVolumeTrade}  TVal={self.TotalValueTrade} {PrxCls}
    Px={self.LastPx}  O={self.OpenPx}  H={self.HighPx}  L={self.LowPx}
    BidWeightPx={self.BidWeightPx}  BidWeightSize={self.BidWeightSize}
    AskWeightPx={self.AskWeightPx}  AskWeightSize={self.AskWeightSize}
    Ask[9]={self.ask[9]}
    Ask[8]={self.ask[8]}
    Ask[7]={self.ask[7]}
    Ask[6]={self.ask[6]}
    Ask[5]={self.ask[5]}
    Ask[4]={self.ask[4]}
    Ask[3]={self.ask[3]}
    Ask[2]={self.ask[2]}
    Ask[1]={self.ask[1]}
    Ask[0]={self.ask[0]}
    --
    Bid[0]={self.bid[0]}
    Bid[1]={self.bid[1]}
    Bid[2]={self.bid[2]}
    Bid[3]={self.bid[3]}
    Bid[4]={self.bid[4]}
    Bid[5]={self.bid[5]}
    Bid[6]={self.bid[6]}
    Bid[7]={self.bid[7]}
    Bid[8]={self.bid[8]}
    Bid[9]={self.bid[9]}
    @{self.TransactTime} ({self.TradingPhase_str})
    AskWeightPx_uncertain={self.AskWeightPx_uncertain}
'''
        else:
            raise Exception(f'Not support SecurityIDSource={self.SecurityIDSource}')

        return s



    @property
    def bytes_stream(self):
        '''将字段打包成字节流'''
        if self.SecurityIDSource == axsbe_base.SecurityIDSource_SZSE:
            #SecurityIDSource=102
            bin = struct.pack("<B", axsbe_base.SecurityIDSource_SZSE)
            #MsgType=111
            bin += struct.pack("<B", self.MsgType)
            #MsgLen=352
            bin += struct.pack("<H", 352)
            #SecurityID=000997
            bin += struct.pack("<9s", ("%06u  "%self.SecurityID).encode('UTF-8'))
            #ChannelNo=1013
            bin += struct.pack("<H", self.ChannelNo)
            #ApplSeqNum=0
            bin += struct.pack("<Q", 0)
            #TradingPhase=83
            bin += struct.pack("<B", self.TradingPhaseCode)
            #NumTrades=0
            bin += struct.pack("<q", self.NumTrades)
            #TotalVolumeTrade=0
            bin += struct.pack("<q", self.TotalVolumeTrade)
            #TotalValueTrade=0
            bin += struct.pack("<q", self.TotalValueTrade)
            #PrevClosePx=184000
            bin += struct.pack("<i", self.PrevClosePx)
            #LastPx=0
            bin += struct.pack("<i", self.LastPx)
            #OpenPx=0
            bin += struct.pack("<i", self.OpenPx)
            #HighPx=0
            bin += struct.pack("<i", self.HighPx)
            #LowPx=0
            bin += struct.pack("<i", self.LowPx)
            #BidWeightPx=0
            bin += struct.pack("<i", self.BidWeightPx)
            #BidWeightSize=0
            bin += struct.pack("<q", self.BidWeightSize)
            #AskWeightPx=0
            bin += struct.pack("<i", self.AskWeightPx)
            #AskWeightSize=0
            bin += struct.pack("<q", self.AskWeightSize)
            #UpLimitPx=20240000
            bin += struct.pack("<i", self.UpLimitPx)
            #DnLimitPx=16560000
            bin += struct.pack("<i", self.DnLimitPx)
            #BidLevel[0].Price=0
            #BidLevel[0].Qty=0
            for i in range(10):
                bin += struct.pack("<i", self.bid[i].Price)
                bin += struct.pack("<q", self.bid[i].Qty)

            #BidLevel[1].Price=0
            #BidLevel[1].Qty=0
            #BidLevel[2].Price=0
            #BidLevel[2].Qty=0
            #BidLevel[3].Price=0
            #BidLevel[3].Qty=0
            #BidLevel[4].Price=0
            #BidLevel[4].Qty=0
            #BidLevel[5].Price=0
            #BidLevel[5].Qty=0
            #BidLevel[6].Price=0
            #BidLevel[6].Qty=0
            #BidLevel[7].Price=0
            #BidLevel[7].Qty=0
            #BidLevel[8].Price=0
            #BidLevel[8].Qty=0
            #BidLevel[9].Price=0
            #BidLevel[9].Qty=0
            #AskLevel[0].Price=0
            #AskLevel[0].Qty=0
            for i in range(10):
                bin += struct.pack("<i", self.ask[i].Price)
                bin += struct.pack("<q", self.ask[i].Qty)
            #AskLevel[1].Price=0
            #AskLevel[1].Qty=0
            #AskLevel[2].Price=0
            #AskLevel[2].Qty=0
            #AskLevel[3].Price=0
            #AskLevel[3].Qty=0
            #AskLevel[4].Price=0
            #AskLevel[4].Qty=0
            #AskLevel[5].Price=0
            #AskLevel[5].Qty=0
            #AskLevel[6].Price=0
            #AskLevel[6].Qty=0
            #AskLevel[7].Price=0
            #AskLevel[7].Qty=0
            #AskLevel[8].Price=0
            #AskLevel[8].Qty=0
            #AskLevel[9].Price=0
            #AskLevel[9].Qty=0
            #TransactTime=20190311083500000
            bin += struct.pack("<Q", self.TransactTime)
            #resv=
            bin += struct.pack("<i", 0)
        elif self.SecurityIDSource == axsbe_base.SecurityIDSource_SSE:
            #SecurityIDSource=102
            bin = struct.pack("<B", axsbe_base.SecurityIDSource_SSE)
            #MsgType=111
            bin += struct.pack("<B", self.MsgType)

            if self.MsgType==axsbe_base.MsgType_snap_stock:
                #MsgLen=336
                bin += struct.pack("<H", 336)
            elif self.MsgType==axsbe_base.MsgType_snap_sse_bond:
                #MsgLen=328
                bin += struct.pack("<H", 328)

            #SecurityID=000997
            bin += struct.pack("<9s", ("%06u  "%self.SecurityID).encode('UTF-8'))
            #ChannelNo=1013
            bin += struct.pack("<H", self.ChannelNo)
            #ApplSeqNum=0
            bin += struct.pack("<Q", 0)
            #TradingPhase=83
            bin += struct.pack("<B", self.TradingPhaseCode)
            #NumTrades=0
            bin += struct.pack("<I", self.NumTrades)
            #TotalVolumeTrade=0
            bin += struct.pack("<q", self.TotalVolumeTrade)
            #TotalValueTrade=0
            bin += struct.pack("<q", self.TotalValueTrade)
            if self.MsgType==axsbe_base.MsgType_snap_stock:
                #PrevClosePx=184000
                bin += struct.pack("<i", self.PrevClosePx)
            #LastPx=0
            bin += struct.pack("<i", self.LastPx)
            #OpenPx=0
            bin += struct.pack("<i", self.OpenPx)
            #HighPx=0
            bin += struct.pack("<i", self.HighPx)
            #LowPx=0
            bin += struct.pack("<i", self.LowPx)
            #BidWeightPx=0
            bin += struct.pack("<i", self.BidWeightPx)
            #BidWeightSize=0
            bin += struct.pack("<q", self.BidWeightSize)
            #AskWeightPx=0
            bin += struct.pack("<i", self.AskWeightPx)
            #AskWeightSize=0
            bin += struct.pack("<q", self.AskWeightSize)
            #DataTimeStamp=92501
            bin += struct.pack("<I", self.TransactTime)
            #BidLevel[0].Price=0
            #BidLevel[0].Qty=0
            for i in range(10):
                bin += struct.pack("<i", self.bid[i].Price)
                bin += struct.pack("<q", self.bid[i].Qty)

            #BidLevel[1].Price=0
            #BidLevel[1].Qty=0
            #BidLevel[2].Price=0
            #BidLevel[2].Qty=0
            #BidLevel[3].Price=0
            #BidLevel[3].Qty=0
            #BidLevel[4].Price=0
            #BidLevel[4].Qty=0
            #BidLevel[5].Price=0
            #BidLevel[5].Qty=0
            #BidLevel[6].Price=0
            #BidLevel[6].Qty=0
            #BidLevel[7].Price=0
            #BidLevel[7].Qty=0
            #BidLevel[8].Price=0
            #BidLevel[8].Qty=0
            #BidLevel[9].Price=0
            #BidLevel[9].Qty=0
            #AskLevel[0].Price=0
            #AskLevel[0].Qty=0
            for i in range(10):
                bin += struct.pack("<i", self.ask[i].Price)
                bin += struct.pack("<q", self.ask[i].Qty)
            #AskLevel[1].Price=0
            #AskLevel[1].Qty=0
            #AskLevel[2].Price=0
            #AskLevel[2].Qty=0
            #AskLevel[3].Price=0
            #AskLevel[3].Qty=0
            #AskLevel[4].Price=0
            #AskLevel[4].Qty=0
            #AskLevel[5].Price=0
            #AskLevel[5].Qty=0
            #AskLevel[6].Price=0
            #AskLevel[6].Qty=0
            #AskLevel[7].Price=0
            #AskLevel[7].Qty=0
            #AskLevel[8].Price=0
            #AskLevel[8].Qty=0
            #AskLevel[9].Price=0
            #AskLevel[9].Qty=0

            if self.MsgType==axsbe_base.MsgType_snap_stock:
                #TradingPhaseCodePack=69
                bin += struct.pack("<B", self.TradingPhaseCodePack)
                #resv=
                bin += struct.pack("<3B", 0, 0 ,0)
        else:
            raise Exception(f'Not support SecurityIDSource={self.SecurityIDSource}')
        return bin


    def unpack_stream(self, bytes_i:bytes):
        '''将消息字节流解包成字段值，重载'''
        #公共头
        self.SecurityIDSource, self.MsgType, _, self.SecurityID, self.ChannelNo, _, self.TradingPhaseCode = struct.unpack("<BBH9sHQB", bytes_i[:24])
        self.SecurityID = int(self.SecurityID[:6])
        #消息体
        if self.SecurityIDSource == axsbe_base.SecurityIDSource_SZSE:
            unpack_token = "<qqqiiiiiiqiqii"
            for i in range(10):
                unpack_token += "iq"
                self.ask[i] = price_level(0,0)
            for i in range(10):
                unpack_token += "iq"
                self.bid[i] = price_level(0,0)
            unpack_token += "Qi"
            self.NumTrades, \
            self.TotalVolumeTrade, \
            self.TotalValueTrade, \
            self.PrevClosePx, \
            self.LastPx, \
            self.OpenPx, \
            self.HighPx, \
            self.LowPx, \
            self.BidWeightPx, \
            self.BidWeightSize, \
            self.AskWeightPx, \
            self.AskWeightSize, \
            self.UpLimitPx, \
            self.DnLimitPx, \
            self.bid[0].Price, \
            self.bid[0].Qty, \
            self.bid[1].Price, \
            self.bid[1].Qty, \
            self.bid[2].Price, \
            self.bid[2].Qty, \
            self.bid[3].Price, \
            self.bid[3].Qty, \
            self.bid[4].Price, \
            self.bid[4].Qty, \
            self.bid[5].Price, \
            self.bid[5].Qty, \
            self.bid[6].Price, \
            self.bid[6].Qty, \
            self.bid[7].Price, \
            self.bid[7].Qty, \
            self.bid[8].Price, \
            self.bid[8].Qty, \
            self.bid[9].Price, \
            self.bid[9].Qty, \
            self.ask[0].Price, \
            self.ask[0].Qty, \
            self.ask[1].Price, \
            self.ask[1].Qty, \
            self.ask[2].Price, \
            self.ask[2].Qty, \
            self.ask[3].Price, \
            self.ask[3].Qty, \
            self.ask[4].Price, \
            self.ask[4].Qty, \
            self.ask[5].Price, \
            self.ask[5].Qty, \
            self.ask[6].Price, \
            self.ask[6].Qty, \
            self.ask[7].Price, \
            self.ask[7].Qty, \
            self.ask[8].Price, \
            self.ask[8].Qty, \
            self.ask[9].Price, \
            self.ask[9].Qty, \
            self.TransactTime, _ =  struct.unpack(unpack_token, bytes_i[24:])
        elif self.SecurityIDSource == axsbe_base.SecurityIDSource_SSE:
            self.NumTrades, \
            self.TotalVolumeTrade, \
            self.TotalValueTrade =  struct.unpack('<iqq', bytes_i[24:44])

            comm_base = 44
            if self.MsgType==axsbe_base.MsgType_snap_stock:
                self.PrevClosePx, =  struct.unpack('<i', bytes_i[44:48])
                comm_base = 48

            unpack_token = "<iiiiiqiqi"
            for i in range(10):
                unpack_token += "iq"
                self.ask[i] = price_level(0,0)
            for i in range(10):
                unpack_token += "iq"
                self.bid[i] = price_level(0,0)

            self.LastPx, \
            self.OpenPx, \
            self.HighPx, \
            self.LowPx, \
            self.BidWeightPx, \
            self.BidWeightSize, \
            self.AskWeightPx, \
            self.AskWeightSize, \
            self.TransactTime, \
            self.bid[0].Price, \
            self.bid[0].Qty, \
            self.bid[1].Price, \
            self.bid[1].Qty, \
            self.bid[2].Price, \
            self.bid[2].Qty, \
            self.bid[3].Price, \
            self.bid[3].Qty, \
            self.bid[4].Price, \
            self.bid[4].Qty, \
            self.bid[5].Price, \
            self.bid[5].Qty, \
            self.bid[6].Price, \
            self.bid[6].Qty, \
            self.bid[7].Price, \
            self.bid[7].Qty, \
            self.bid[8].Price, \
            self.bid[8].Qty, \
            self.bid[9].Price, \
            self.bid[9].Qty, \
            self.ask[0].Price, \
            self.ask[0].Qty, \
            self.ask[1].Price, \
            self.ask[1].Qty, \
            self.ask[2].Price, \
            self.ask[2].Qty, \
            self.ask[3].Price, \
            self.ask[3].Qty, \
            self.ask[4].Price, \
            self.ask[4].Qty, \
            self.ask[5].Price, \
            self.ask[5].Qty, \
            self.ask[6].Price, \
            self.ask[6].Qty, \
            self.ask[7].Price, \
            self.ask[7].Qty, \
            self.ask[8].Price, \
            self.ask[8].Qty, \
            self.ask[9].Price, \
            self.ask[9].Qty =  struct.unpack(unpack_token, bytes_i[comm_base:comm_base+284])

            if self.MsgType==axsbe_base.MsgType_snap_stock:
                self.TradingPhaseCodePack, _, _, _ =  struct.unpack('4B', bytes_i[332:])
        else:
            raise Exception(f'Not support SecurityIDSource={self.SecurityIDSource}')

    @property
    def ccode(self):
        '''打印与hls c相同格式的日志，重载'''
        if self.SecurityIDSource == axsbe_base.SecurityIDSource_SZSE:
            s = f'''
    snap.Header.SecurityIDSource = __SecurityIDSource_SSZ_;
    snap.Header.MsgType = __MsgType_SSZ_INSTRUMENT_SNAP__;
    snap.Header.MsgLen = BITSIZE_SBE_SSZ_instrument_snap_t_packed / 8;
    setSecurityID(snap.Header.SecurityID, "{"%06d"%self.SecurityID}");
    snap.Header.ChannelNo = {self.ChannelNo};
    snap.Header.ApplSeqNum = 0;
    snap.Header.TradingPhase.Code0 = {self.TradingPhaseCode%0xf};
    snap.Header.TradingPhase.Code1 = {self.TradingPhaseCode>>4};

    snap.NumTrades = {self.NumTrades};
    snap.TotalVolumeTrade = {self.TotalVolumeTrade};
    snap.TotalValueTrade = {self.TotalValueTrade};
    snap.PrevClosePx = {self.PrevClosePx};
    snap.LastPx = {self.LastPx};
    snap.OpenPx = {self.OpenPx};
    snap.HighPx = {self.HighPx};
    snap.LowPx = {self.LowPx};
    snap.BidWeightPx = {self.BidWeightPx};
    snap.BidWeightSize = {self.BidWeightSize};
    snap.AskWeightPx = {self.AskWeightPx};
    snap.AskWeightSize = {self.AskWeightSize};
    snap.UpLimitPx = {self.UpLimitPx};
    snap.DnLimitPx = {self.DnLimitPx};'''

            for i in range(len(self.bid)):
                s += f'''
    snap.BidLevel[{i}].Price = {self.bid[i].Price};
    snap.BidLevel[{i}].Qty = {self.bid[i].Qty};'''
            for i in range(len(self.ask)):
                s += f'''
    snap.AskLevel[{i}].Price = {self.ask[i].Price};
    snap.AskLevel[{i}].Qty = {self.ask[i].Qty};'''
            s += f'''
    snap.TransactTime = {self.TransactTime};
    snap.Resv[0] = 0;
    snap.Resv[1] = 0;
    snap.Resv[2] = 0;
    snap.Resv[3] = 0;
'''

        elif self.SecurityIDSource == axsbe_base.SecurityIDSource_SSE:
            if self.MsgType==axsbe_base.MsgType_snap_stock:
                mType = '__MsgType_SSZ_INSTRUMENT_SNAP__' 
                mSize = 'BITSIZE_SBE_SSH_instrument_snap_t_packed'
                prevPx = f'snap.PrevClosePx = {self.PrevClosePx};'
                weight_name = 'BidWeightPx', 'BidWeightSize', 'AskWeightPx', 'AskWeightSize'
            else:
                mType = '__MsgType_SSH_BOND_SNAP__' 
                mSize = 'BITSIZE_SBE_SSH_bond_snap_t_packed'
                prevPx = ''
                weight_name = 'AltWeightedAvgBidPx', 'TotalBidQty', 'AltWeightedAvgOfferPx', 'TotalOfferQty'

            s = f'''
    snap.Header.SecurityIDSource = __SecurityIDSource_SSH_;
    snap.Header.MsgType = {mType};
    snap.Header.MsgLen = {mSize} / 8;
    setSecurityID(snap.Header.SecurityID, "{"%06d"%self.SecurityID}");
    snap.Header.ChannelNo = {self.ChannelNo};
    snap.Header.ApplSeqNum = 0;
    snap.Header.TradingPhase = {self.TradingPhaseCode};

    snap.NumTrades = {self.NumTrades};
    snap.TotalVolumeTrade = {self.TotalVolumeTrade};
    snap.TotalValueTrade = {self.TotalValueTrade};
    {prevPx}
    snap.LastPx = {self.LastPx};
    snap.OpenPx = {self.OpenPx};
    snap.HighPx = {self.HighPx};
    snap.LowPx = {self.LowPx};
    snap.{weight_name[0]} = {self.BidWeightPx};
    snap.{weight_name[1]} = {self.BidWeightSize};
    snap.{weight_name[2]} = {self.AskWeightPx};
    snap.{weight_name[3]} = {self.AskWeightSize};
    snap.DataTimeStamp = {self.TransactTime};'''

            for i in range(len(self.bid)):
                s += f'''
    snap.BidLevel[{i}].Price = {self.bid[i].Price};
    snap.BidLevel[{i}].Qty = {self.bid[i].Qty};'''
            for i in range(len(self.ask)):
                s += f'''
    snap.AskLevel[{i}].Price = {self.ask[i].Price};
    snap.AskLevel[{i}].Qty = {self.ask[i].Qty};'''

            if self.MsgType==MsgType_snap_stock:
                s += f'''
    snap.TradingPhaseCodePack.B1 = {self.TradingPhaseCodePack>>6};
    snap.TradingPhaseCodePack.B2 = {(self.TradingPhaseCodePack>>2)&0xf};
    snap.TradingPhaseCodePack.B3 = {(self.TradingPhaseCodePack)&0x3};
    snap.Resv[0] = 0;
    snap.Resv[1] = 0;
    snap.Resv[2] = 0;
'''

        else:
            raise Exception(f'Not support SecurityIDSource={self.SecurityIDSource}')
        return s
        
    def save(self):
        '''save/load 用于保存/加载测试时刻'''
        data = {}
        for attr in self.__slots__:
            value = getattr(self, attr)
            if attr == 'bid' or attr == 'ask':
                data[attr] = {}
                for i in value:
                    data[attr][i] = value[i].save()
            else:
                data[attr] = value
        return data

    def load(self, data):
        for attr in self.__slots__:
            if attr == 'bid' or attr == 'ask':
                v = {}
                for i in data[attr]:
                    v[i] = price_level(-1, -1)
                    v[i].load(data[attr][i])
                setattr(self, attr, v)
            else:
                setattr(self, attr, data[attr])
