# -*- coding: utf-8 -*-
"""PowerCollector EXE 单文件 的 Windows PE 版本信息（版权/作者/公司）。

PyInstaller 把此内容打进 exe 的 VersionResource：
  - 资源管理器"属性 → 详细信息"页可见 公司/版权/产品名/文件描述
  - 部分软件分发渠道 / 杀软白名单读取这些信息
"""
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)

# 公司/版权（晨曦网络工作室 · 重庆亦静怡动电子商务有限公司）
_COMPANY   = '晨曦网络工作室  重庆亦静怡动电子商务有限公司'
_COPYRIGHT = u'© 晨曦网络工作室  重庆亦静怡动电子商务有限公司  All rights reserved.'
_PRODUCT   = u'电力低压数据采集软件'
_FILEDESC  = u'电力低压数据采集软件（3D 地球 + ArcGIS 卫星影像 + Excel 自动设备识别 + 拓扑层级）'
_ORIGNAME  = '电力低压数据采集软件.exe'

info = VSVersionInfo(
    kids=[
        StringFileInfo([
            StringTable('040904B0', [   # 简体中文 (0409) + 代码页 1200(0x04B0)
                StringStruct('CompanyName',  _COMPANY),
                StringStruct('FileDescription', _FILEDESC),
                StringStruct('FileVersion',     '1.1.1.0'),
                StringStruct('LegalCopyright',  _COPYRIGHT),
                StringStruct('OriginalFilename', _ORIGNAME),
                StringStruct('ProductName',     _PRODUCT),
                StringStruct('ProductVersion',  '1.1.1.0'),
                StringStruct('InternalName',    'powercollector'),
            ]),
        ]),
        VarFileInfo([
            VarStruct('Translation', [0x0409, 0x04B0]),
        ]),
    ],
    ffi=FixedFileInfo(
        filevers=(1, 1, 1, 0),
        prodvers=(1, 1, 1, 0),
        mask=0x3f,
        flags=0,
        OS=0x00040000,     # 64-bit
        fileType=2,        # 应用程序
        subtype=0,
        date=(0, 0),
    ),
)
