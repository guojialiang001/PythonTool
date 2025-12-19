# 在其他脚本中导入使用
from set_claude_env import set_config, list_configs

# 基础使用
set_config('avoapi', user_level=True)  # 系统级设置
# set_config('a', user_level=True)  # 用户级设置

# 高级用法
# set_config('test', preview=True)  # 预览
# set_config('custom', config_file='custom.json')  # 指定文件

# 获取配置列表
configs = list_configs()  # 返回配置名称列表
