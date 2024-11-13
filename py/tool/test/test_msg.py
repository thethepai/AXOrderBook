# -*- coding: utf-8 -*-

from tool.test_util import *
from tool.msg_util import *
import os
import json

def TEST_msg_byte_stream_mkt(market):
    ## test: byte_stream
    print(f'market = {market} order.size = {len(axsbe_order(market).bytes_stream)} Bytes')
    print(f'market = {market}  exec.size = {len(axsbe_exe(market).bytes_stream)} Bytes')
    print(f'market = {market}  snap.size = {len(axsbe_snap_stock(market).bytes_stream)} Bytes')
    if market==axsbe_base.SecurityIDSource_SSE:
        print(f'market = {market} order-bond-add.size = {len(axsbe_order(market, MsgType=axsbe_base.MsgType_order_sse_bond_add).bytes_stream)} Bytes')
        print(f'market = {market} order-bond-del.size = {len(axsbe_order(market, MsgType=axsbe_base.MsgType_order_sse_bond_del).bytes_stream)} Bytes')
        print(f'market = {market} order-bond-exe.size = {len(axsbe_exe(market, MsgType=axsbe_base.MsgType_exe_sse_bond).bytes_stream)} Bytes')
        print(f'market = {market} order-bond-status.size = {len(axsbe_status(market, MsgType=axsbe_base.MsgType_status_sse_bond).bytes_stream)} Bytes')
        print(f'market = {market}  snap-bond.size     = {len(axsbe_snap_stock(market, MsgType=axsbe_base.MsgType_snap_sse_bond).bytes_stream)} Bytes')

def TEST_msg_byte_stream():
    TEST_msg_byte_stream_mkt(axsbe_base.SecurityIDSource_SZSE)
    TEST_msg_byte_stream_mkt(axsbe_base.SecurityIDSource_SSE)



def TEST_msg_SL_mkt(market):
    ## test: save/load
    data = axsbe_order(market).save()
    print(data)
    if market==axsbe_base.SecurityIDSource_SZSE:
        data['OrdType'] = ord('U')
        data['Side'] = ord('F')
    elif market==axsbe_base.SecurityIDSource_SSE:
        data['OrdType'] = ord('A')
        data['Side'] = ord('B')
    order = axsbe_order()
    order.load(data)
    print(order)

    data = axsbe_exe(market).save()
    print(data)
    if market==axsbe_base.SecurityIDSource_SZSE:
        data['ExecType'] = ord('F')
    elif market==axsbe_base.SecurityIDSource_SSE:
        data['ExecType'] = ord('B')
    exe = axsbe_exe()
    exe.load(data)
    print(exe)

    data = axsbe_snap_stock(market).save()
    print(data)
    snap = axsbe_snap_stock()
    data['ask'][0]['Price'] = 22200
    data['ask'][0]['Qty'] = 10000
    data['bid'][1]['Price'] = 111
    data['bid'][1]['Qty'] = 20000
    snap.load(data)
    print(snap)

    if market==axsbe_base.SecurityIDSource_SSE:
        data = axsbe_order(market, MsgType=axsbe_base.MsgType_order_sse_bond_add).save()
        print(data)
        data['Side'] = ord('S')
        order = axsbe_order()
        order.load(data)
        print(order)

        data = axsbe_order(market, MsgType=axsbe_base.MsgType_order_sse_bond_del).save()
        print(data)
        data['Side'] = ord('B')
        order = axsbe_order()
        order.load(data)
        print(order)

        data = axsbe_exe(market, MsgType=axsbe_base.MsgType_exe_sse_bond).save()
        print(data)
        data['ExecType'] = ord('B')
        exec = axsbe_exe()
        exec.load(data)
        print(exec)

        data = axsbe_status(market, MsgType=axsbe_base.MsgType_status_sse_bond).save()
        print(data)
        data['TradingPhaseInstrument'] = 2
        status = axsbe_status()
        status.load(data)
        print(status)

        data = axsbe_snap_stock(market, MsgType=axsbe_base.MsgType_snap_sse_bond).save()
        print(data)
        snap = axsbe_snap_stock()
        data['ask'][0]['Price'] = 33300
        data['ask'][0]['Qty'] = 99999
        data['bid'][1]['Price'] = 888
        data['bid'][1]['Qty'] = 777
        snap.load(data)
        print(snap)

def TEST_msg_SL():
    TEST_msg_SL_mkt(axsbe_base.SecurityIDSource_SZSE)
    TEST_msg_SL_mkt(axsbe_base.SecurityIDSource_SSE)



def TEST_msg_ms_mkt(market, instrument_type, TEST_NB = 100):
    '''
    打印消息时戳
    
    深交所:用TEST_NB>35000可以看到快照数据的时戳在 09:41:21 之前的都比逐笔的时戳早，说明逐笔行情的传输被阻塞了
    '''

    if market==axsbe_base.SecurityIDSource_SZSE:
        save_log = 'ms_szse.log'
        load_sbe = os.path.join('data', '20220422', 'AX_sbe_szse_000001.log')
    elif market==axsbe_base.SecurityIDSource_SSE:
        if instrument_type==INSTRUMENT_TYPE.STOCK:
            save_log = 'ms_sse-stock.log'
            load_sbe = os.path.join('data', '20230207', 'AX_sbe_sse_600519.log')
        else:#KZZ
            save_log = 'ms_sse-bond.log'
            load_sbe = os.path.join('data', '20230207', 'AX_sbe_sse_110068.log')


    f = open(os.path.join('log', save_log), "w", encoding='utf-8')

    n = 0
    for msg in axsbe_file(load_sbe):
        # print(msg.ms)
        if msg.MsgType in axsbe_base.MsgTypes_order:
            f.write(f"{n:6d}\torder  {msg.ms}\t{msg.tick}\n")
        elif msg.MsgType in axsbe_base.MsgTypes_exe:
            f.write(f"{n:6d}\texe    {msg.ms}\t{msg.tick}\n")
        elif msg.MsgType in axsbe_base.MsgTypes_headerOnly:
            f.write(f"{n:6d}\tstatus {msg.ms}\t{msg.tick}\t{msg.TradingPhase_str}\n")
        else:
            f.write(f"{n:6d}\tsnap   {msg.ms}\t{msg.tick}\t{msg.TradingPhase_str}\n")
        n += 1
        if n>=TEST_NB:
            break

    f.close()
    print("TEST_msg_ms done")
    return

def TEST_msg_ms(TEST_NB = 100):
    TEST_msg_ms_mkt(axsbe_base.SecurityIDSource_SZSE, INSTRUMENT_TYPE.STOCK, TEST_NB)
    TEST_msg_ms_mkt(axsbe_base.SecurityIDSource_SSE, INSTRUMENT_TYPE.STOCK, TEST_NB)
    TEST_msg_ms_mkt(axsbe_base.SecurityIDSource_SSE, INSTRUMENT_TYPE.BOND, TEST_NB)



@timeit
def TEST_serial_mkt(market, instrument_type, TEST_NB = 100):
    '''
    测试numpy字节流的打包/解包

    5950x + 860evo:
        sz000001:tested_exe=106434 tested_order=122359 tested_snap=5082; sum=233875; used~4.6s
                 peak order(bid+ask)=56047; peak pxlv(bid+ask)=338
    '''
    if market==axsbe_base.SecurityIDSource_SZSE:
        load_sbe = os.path.join('data', '20220422', 'AX_sbe_szse_000001.log')
    elif market==axsbe_base.SecurityIDSource_SSE:
        if instrument_type==INSTRUMENT_TYPE.STOCK:
            load_sbe = os.path.join('data', '20230207', 'AX_sbe_sse_600519.log')
        else:#KZZ
            load_sbe = os.path.join('data', '20230207', 'AX_sbe_sse_110068.log')

    tested_order = 0
    tested_exe = 0
    tested_snap = 0
    tested_status = 0

    unpack_axsbe_order = axsbe_order()
    unpack_axsbe_execute = axsbe_exe()
    unpack_axsbe_snap_stock = axsbe_snap_stock()
    unpack_axsbe_status_stock = axsbe_status()

    for msg in axsbe_file(load_sbe):
        if msg.MsgType in axsbe_base.MsgTypes_order:
            bytes_np = msg.bytes_np
            unpack_axsbe_order.unpack_np(bytes_np)
            if str(msg) != str(unpack_axsbe_order):
                print('--before pack--')
                print(msg)
                print('--after pack/unpack--')
                print(unpack_axsbe_order)
                raise RuntimeError("TEST_serial tested_order NG")
            tested_order += 1
        elif msg.MsgType in axsbe_base.MsgTypes_exe:
            bytes_np = msg.bytes_np
            unpack_axsbe_execute.unpack_np(bytes_np)
            if str(msg) != str(unpack_axsbe_execute):
                print('--before pack--')
                print(msg)
                print('--after pack/unpack--')
                print(unpack_axsbe_execute)
                raise RuntimeError("TEST_serial tested_exe NG")
            tested_exe += 1
        elif msg.MsgType in axsbe_base.MsgTypes_snap:
            bytes_np = msg.bytes_np
            unpack_axsbe_snap_stock.unpack_np(bytes_np)
            if str(msg) != str(unpack_axsbe_snap_stock):
                print('--before pack--')
                print(msg)
                print('--after pack/unpack--')
                print(unpack_axsbe_snap_stock)
                raise RuntimeError("TEST_serial tested_snap NG")
            tested_snap += 1
        elif msg.MsgType in axsbe_base.MsgTypes_headerOnly:
            bytes_np = msg.bytes_np
            unpack_axsbe_status_stock.unpack_np(bytes_np)
            if str(msg) != str(unpack_axsbe_status_stock):
                print('--before pack--')
                print(msg)
                print('--after pack/unpack--')
                print(unpack_axsbe_status_stock)
                raise RuntimeError("TEST_serial tested_status NG")
            tested_status += 1

        if tested_exe>=TEST_NB and tested_order>=TEST_NB and tested_snap>=TEST_NB:
            break
    print(f"TEST_serial done"
          f" tested_exe={tested_exe} tested_order={tested_order} tested_snap={tested_snap} tested_status={tested_status};"
          f" sum={tested_exe+tested_order+tested_snap}")
    return

def TEST_serial(TEST_NB = 100):
    # TEST_serial_mkt(axsbe_base.SecurityIDSource_SZSE, INSTRUMENT_TYPE.STOCK, TEST_NB)
    # TEST_serial_mkt(axsbe_base.SecurityIDSource_SSE, INSTRUMENT_TYPE.STOCK, TEST_NB)
    TEST_serial_mkt(axsbe_base.SecurityIDSource_SSE, INSTRUMENT_TYPE.BOND, TEST_NB)



@timeit
def TEST_msg_ms_filt(source_log, securityID, read_nb=0, print_nb = 100):
    '''
    打印消息时戳，指定证券代码
    
    '''
    if not os.path.exists(source_log):
        raise f"{source_log} not exists"

    f = open("log/TEST_msg_ms_filt.log", "w")

    securityID = int(securityID)

    pn = 0
    rn = 0
    for msg in axsbe_file(source_log):
        rn += 1
        if read_nb>0:
            if rn >= read_nb:
                break

        if msg.SecurityID != securityID:
            continue
        # print(msg.ms)
        if msg.MsgType in axsbe_base.MsgTypes_order:
            f.write(f"{rn:6d}\t{securityID:06d}\torder {msg.ms}\t{msg.tick}\n")
        elif msg.MsgType==axsbe_base.MsgType_exe:
            f.write(f"{rn:6d}\t{securityID:06d}\texe   {msg.ms}\t{msg.tick}\n")
        else:
            f.write(f"{rn:6d}\t{securityID:06d}\tsnap  {msg.ms}\t{msg.tick}\n")
        pn += 1
        if pn>=print_nb:
            break

    f.close()
    print("TEST_msg_ms_filt done")
    return


@timeit
def TEST_msg_text(source_log, securityID, read_nb=0, print_nb = 100):
    '''
    打印消息文本，指定证券代码
    '''
    if not os.path.exists(source_log):
        raise f"{source_log} not exists"

    f = open("log/TEST_msg_text.log", "w")

    securityID = int(securityID)

    pn = 0
    rn = 0
    for msg in axsbe_file(source_log):
        rn += 1
        if read_nb>0:
            if rn >= read_nb:
                break

        if msg.SecurityID != securityID:
            continue
        # print(msg.ms)
        f.write(f"{rn:6d}\n")
        f.write(str(msg))
        pn += 1
        if pn>=print_nb:
            break

    f.close()
    print("TEST_msg_text done")
    return


@timeit
def TEST_print_securityID(source_log, read_nb=0, instrument_type=INSTRUMENT_TYPE.UNKNOWN):
    '''
    统计文件内的证券代码
    instrument_type 统计对象，目前只支持股票或全部；当为股票时只能过滤深圳的股票
    '''
    if not os.path.exists(source_log):
        raise f"{source_log} not exists"

    with open("log/TEST_print_securityID.log", "w") as f:
        f.write(f'{source_log}\n')
        f.flush()

        securityIDs = {}

        rn = 0
        soc = 0
        eoc = 0
        for msg in axsbe_file(source_log):
            rn += 1
            if read_nb>0:
                if rn >= read_nb:
                    break

            # 当要求只统计股票，且是深圳代码时，去掉非股票的
            if instrument_type==INSTRUMENT_TYPE.STOCK:
                if msg.SecurityIDSource==SecurityIDSource_SZSE:
                    if msg.ChannelNo>=1010 and msg.ChannelNo<=1019: #股票快照
                        pass
                    elif msg.ChannelNo>=2010 and msg.ChannelNo<=2019: #股票逐笔
                        pass
                    else:
                        continue #去掉非股票

            if msg.HHMMSSms<91500000:
                soc = 1

            if msg.HHMMSSms>92800000:
                eoc = 1

            if soc and eoc:
                break

            if msg.SecurityID not in securityIDs:
                securityIDs[msg.SecurityID] = {
                    'snap':0,
                    'snap_ts':0,
                    'inc':0,
                    'inc_ts':0,
                    'UpLimitPx':0,
                    'DnLimitPx':0,
                    'ChannelNo':0,
                }
            securityIDs[msg.SecurityID]['ChannelNo'] = msg.ChannelNo
            if isinstance(msg, axsbe_snap_stock):
                securityIDs[msg.SecurityID]['snap'] += 1
                securityIDs[msg.SecurityID]['snap_ts'] = msg.TransactTime
                securityIDs[msg.SecurityID]['UpLimitPx'] = msg.UpLimitPx
                securityIDs[msg.SecurityID]['DnLimitPx'] = msg.DnLimitPx
            elif isinstance(msg, axsbe_exe) or isinstance(msg, axsbe_order):
                securityIDs[msg.SecurityID]['inc'] += 1
                securityIDs[msg.SecurityID]['inc_ts'] = msg.TransactTime

        f.write(f"{json.dumps(securityIDs, indent=4)}\n")


        # 逐笔最少和最多的前100只个股
        securityIDs = {k:v for k,v in securityIDs.items() if v['inc']!=0} #剔除0逐笔
        u = sorted(securityIDs.items(),key=lambda x:x[1]['inc'])

        all_inc = [x[0] for x in u]
        min_inc = all_inc[:100]
        max_inc = all_inc[-100:]

        f.write(f'all_inc={all_inc}\n')
        f.write(f'min_inc={min_inc}\n')
        f.write(f'max_inc={max_inc}\n')

    print("TEST_print_securityID done")
    return all_inc, min_inc, max_inc


@timeit
def TEST_ApplSeqNum(source_log, read_nb=0):
    '''
    统计文件内的证券代码
    '''
    if not os.path.exists(source_log):
        raise f"{source_log} not exists"

    with open("log/TEST_ApplSeqNum.log", "w") as f:
        f.write(f'{source_log}\n')
        f.flush()

        channleId = {}

        rn = 0
        for msg in axsbe_file(source_log):
            rn += 1
            if read_nb>0:
                if rn >= read_nb:
                    break

            if isinstance(msg, axsbe_exe) or isinstance(msg, axsbe_order):
                if msg.ChannelNo not in channleId:
                    channleId[msg.ChannelNo] = {
                        'min':0,
                        'max':0,
                        'last':0,
                        'miss':[],
                        'reorder':[],
                        'dunpliacte':0,
                    }
                s = msg.ApplSeqNum
                if channleId[msg.ChannelNo]['min']==0:
                    channleId[msg.ChannelNo]['min'] = s
                    channleId[msg.ChannelNo]['max'] = s
                    channleId[msg.ChannelNo]['last'] = s
                else:
                    channleId[msg.ChannelNo]['min'] = min(channleId[msg.ChannelNo]['min'], s)
                    channleId[msg.ChannelNo]['max'] = max(channleId[msg.ChannelNo]['max'], s)
                    if s in channleId[msg.ChannelNo]['miss']:
                        channleId[msg.ChannelNo]['miss'].remove(s)
                        channleId[msg.ChannelNo]['reorder'].append(s)
                    if s != channleId[msg.ChannelNo]['last']+1:
                        if s==channleId[msg.ChannelNo]['last']:
                            channleId[msg.ChannelNo]['dunpliacte'] += 1
                        channleId[msg.ChannelNo]['miss'].extend(range(channleId[msg.ChannelNo]['last']+1, s))
                    channleId[msg.ChannelNo]['last'] = s

        f.write(f"{json.dumps(channleId, indent=4)}\n")

    print("TEST_print_securityID done")
    return

