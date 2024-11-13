# -*- coding: utf-8 -*-

# require python 3.8+
# require ownership about folder "bin/.run" under python directory, such as "/data/anaconda3/bin/.run".

import pynq
from pynq import Device

import numpy as np
np.set_printoptions(formatter={'int': hex}, threshold=1000)

import os

####### 通用信息 #######
# shell应为 xilinx_u50_gen3x16_xdma_base_5
devices = Device.devices
for i in range(len(devices)):
    print("{}) {}".format(i, devices[i].name))

# 装载xclbin
bin_file = 'hbmArbiter_2_2_2_128m_test_hw.xclbin'
if "XCL_EMULATION_MODE" in os.environ:
    assert os.environ['XCL_EMULATION_MODE'] == 'hw_emu', f"Only support env(XCL_EMULATION_MODE)=hw_emu, current is {os.environ['XCL_EMULATION_MODE']}"
    bin_file = 'hbmArbiter_2_2_2_128m_test_hw_emu.xclbin'
ol = pynq.Overlay(bin_file)

# 时钟信息
print(ol.clock_dict)

# CU列表
print(ol.ip_dict.keys())

# CU寄存器值
for k in ol.ip_dict:
    print("{}.{}".format(k, ol.__getattr__(k).register_map))

# CU调用接口信息
for k in ol.ip_dict:
    print("{}{}".format(k, ol.__getattr__(k).signature))


####### 用于对接void*寄存器 #######
DMY = pynq.allocate((1, 1), dtype='u4')

####### kernel 执行 #######
hbmArbiter = ol.hbmArbiter_2_2_2_128m_top_1
mu0 = ol.mu0
mu1 = ol.mu1

lm_mu0_rd0 = ol.lm_mu0_rd0
lm_mu0_rd1 = ol.lm_mu0_rd1

lm_mu1_rd0 = ol.lm_mu1_rd0
lm_mu1_rd1 = ol.lm_mu1_rd1


mu0s = mu0.start(
    reg_guard_bgn = DMY, 
    wk_nb = 16, 
    min_addr = 0,
    max_addr = 32, 
    min_data = 0,
    gap_nb = 4, 
    wr0_wk_nb = DMY,
    wr1_wk_nb = DMY,
    rd0_wk_nb = DMY,
    rd1_wk_nb = DMY,
    rdo0_rx_nb = DMY,
    rdo1_rx_nb = DMY,
    rd0err_nb = DMY,
    rd1err_nb = DMY,
    gap_wk_nb = DMY,
    reg_guard_end = DMY
)

mu0s.wait()


# CU寄存器值
for k in ol.ip_dict:
    print("{}.{}".format(k, ol.__getattr__(k).register_map))

# lm_mu0_rd0 寄存器地址
for r in lm_mu0_rd0._registers:
    print('lm_mu0_rd0.{}.offset={}'.format(r, lm_mu0_rd0._registers[r]['address_offset']))

# 通过改历史记录ram地址，读取历史记录
offset = lm_mu0_rd0._registers['history_id']['address_offset']
for i in range(16):
    lm_mu0_rd0.write(offset, i)
    print(lm_mu0_rd0.register_map)

###### clean up ######
del DMY
