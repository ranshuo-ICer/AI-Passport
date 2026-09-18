"""PassportOS 引导脚本 —— 在 main.py 之前运行。

只做最必要的事：确保目录存在、把库路径挂上。
不要在这里做耗时操作，否则会拖慢启动。
"""

import os
import sys
import gc

sys.path.append("/")
sys.path.append("/passport")

for d in ("/apps",):
    try:
        os.mkdir(d)
    except OSError:
        pass

gc.collect()
