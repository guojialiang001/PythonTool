#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
import os
import argparse
from pathlib import Path
from typing import Dict, Any, Optional, List

def is_admin():
    """检查是否管理员权限"""
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def load_configurations(json_file: str) -> Optional[Dict[str, Any]]:
    """加载JSON配置文件并过滤注释字段

    Args:
        json_file: 配置文件路径

    Returns:
        配置字典或None（失败时）
    """
    try:
        # 支持IDEA传递的路径格式（去除引号）
        json_file = str(json_file).strip('"').strip("'")

        if not os.path.exists(json_file):
            print(f"❌ 配置文件不存在: {json_file}")
            return None

        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 过滤掉以_开头的注释字段
        configs = {k: v for k, v in data.items() if not k.startswith('_')}
        return configs
    except json.JSONDecodeError as e:
        print(f"❌ JSON格式错误: {e}")
        return None
    except Exception as e:
        print(f"❌ 读取配置文件失败: {e}")
        return None

def set_env_variables(config: Dict[str, Any], is_user: bool = False,
                     preview: bool = False, verbose: bool = True) -> bool:
    """设置环境变量

    Args:
        config: 配置字典
        is_user: 是否用户级（默认系统级）
        preview: 仅预览不实际设置
        verbose: 是否输出详细信息

    Returns:
        是否成功
    """
    if not config:
        if verbose:
            print("❌ 配置为空")
        return False

    scope = "用户" if is_user else "系统"

    if preview:
        if verbose:
            print(f"\n{'='*60}")
            print(f"【预览模式】将要设置{scope}环境变量:")
            print(f"{'='*60}")
            for key, value in config.items():
                str_value = str(value) if value is not None else ""
                print(f"  {key} = {str_value}")
            print(f"\n共 {len(config)} 个变量")
        return True

    if verbose:
        print(f"\n正在设置{scope}环境变量...")

    success_count = 0
    failed_items = []

    for key, value in config.items():
        # 确保值是字符串
        str_value = str(value) if value is not None else ""

        try:
            if is_user:
                # 用户级环境变量
                cmd = f'setx {key} "{str_value}" > nul'
            else:
                # 系统级环境变量
                cmd = f'setx {key} "{str_value}" /M > nul'

            exit_code = os.system(cmd)
            if exit_code == 0:
                if verbose:
                    print(f"  ✅ {key} = {str_value}")
                success_count += 1
            else:
                if verbose:
                    print(f"  ❌ {key} 设置失败")
                failed_items.append(key)
        except Exception as e:
            if verbose:
                print(f"  ❌ {key} 设置失败: {e}")
            failed_items.append(key)

    if verbose:
        print(f"\n✅ 成功设置 {success_count}/{len(config)} 个环境变量")

        if failed_items:
            print(f"❌ 失败的变量: {', '.join(failed_items)}")

        print("⚠️  注意: 新环境变量需要新开命令行窗口才能生效")

    return len(failed_items) == 0

def interactive_mode(configs: Dict[str, Any], batch_mode: bool = False) -> tuple:
    """交互式选择模式

    Args:
        configs: 配置字典
        batch_mode: 批处理模式

    Returns:
        (配置名称, 是否用户级)
    """
    if batch_mode:
        print("❌ 批处理模式下需要指定配置名称 (-c)")
        return None, False

    print("\n" + "="*50)
    print("可用配置列表:")
    print("="*50)

    config_names = list(configs.keys())
    for i, name in enumerate(config_names, 1):
        print(f"{i}. {name}")

    print("\nq. 退出")
    print("="*50)

    while True:
        choice = input("\n请选择配置 (输入编号): ").strip()
        if choice.lower() == 'q':
            sys.exit(0)

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(config_names):
                selected = config_names[idx]
                break
            else:
                print("❌ 无效的选择，请重新输入")
        except ValueError:
            print("❌ 请输入数字")

    # 询问权限级别
    scope_choice = input("设置级别: 1.系统(需管理员)  2.用户  (默认1): ").strip() or "1"
    is_user = scope_choice == "2"

    return selected, is_user

def parse_config_name(config_str: str) -> Optional[str]:
    """解析配置名称，支持多种格式

    Args:
        config_str: 配置名称字符串

    Returns:
        清理后的配置名称
    """
    if not config_str:
        return None

    # 去除引号和空格
    config_str = config_str.strip('"').strip("'").strip()

    return config_str if config_str else None

def set_config(config_name: str, config_file: str = 'env_config.json',
               user_level: bool = False, preview: bool = False,
               verbose: bool = True) -> bool:
    """方法调用接口 - 设置指定配置

    Args:
        config_name: 配置名称
        config_file: 配置文件路径（默认: env_config.json）
        user_level: 是否用户级（默认系统级）
        preview: 仅预览
        verbose: 是否输出信息

    Returns:
        是否成功

    示例:
        # 系统级设置
        set_config('s')

        # 用户级设置
        set_config('a', user_level=True)

        # 预览模式
        set_config('test', preview=True)

        # 指定配置文件
        set_config('custom', config_file='custom.json', user_level=True)
    """
    # 加载配置
    configs = load_configurations(config_file)
    if configs is None:
        return False

    # 验证配置
    if config_name not in configs:
        if verbose:
            print(f"❌ 配置 '{config_name}' 不存在")
            print(f"可用配置: {', '.join(configs.keys())}")
        return False

    # 检查权限
    if not user_level:
        if not is_admin():
            if verbose:
                print("❌ 设置系统环境变量需要管理员权限")
                print("请:")
                print("  1. 以管理员身份运行，或")
                print("  2. 设置 user_level=True")
            return False

    # 执行设置
    if verbose:
        print(f"\n{'='*50}")
        print(f"配置: {config_name}")
        print(f"级别: {'用户' if user_level else '系统'}")
        if preview:
            print("模式: 预览")
        print(f"{'='*50}")

    config = configs[config_name]
    return set_env_variables(config, user_level, preview, verbose)

def list_configs(config_file: str = 'env_config.json', verbose: bool = True) -> List[str]:
    """列出所有配置名称

    Args:
        config_file: 配置文件路径
        verbose: 是否输出信息

    Returns:
        配置名称列表
    """
    configs = load_configurations(config_file)
    if configs is None:
        return []

    if verbose:
        print("\n可用配置:")
        for name in configs:
            print(f"  - {name}")

    return list(configs.keys())

def main():
    """主入口 - 命令行模式"""
    parser = argparse.ArgumentParser(
        description="Claude API环境变量配置工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 命令行模式
  python set_claude_env.py -c s                    # 系统级
  python set_claude_env.py -c a --user             # 用户级
  python set_claude_env.py --list                  # 列出配置

  # 方法调用模式（在其他Python代码中）
  from set_claude_env import set_config, list_configs

  # 设置配置
  set_config('s')
  set_config('a', user_level=True)

  # 预览
  set_config('test', preview=True)

  # IDEA 兼容
  set_config('s', config_file='C:\\path\\to\\env_config.json', user_level=True)
        """
    )

    parser.add_argument('-c', '--config', help='配置名称 (s/a/test/custom)')
    parser.add_argument('-f', '--file', help='JSON配置文件路径 (默认: env_config.json)')
    parser.add_argument('--user', '-u', action='store_true', help='设置用户级环境变量')
    parser.add_argument('--list', '-l', action='store_true', help='列出所有配置')
    parser.add_argument('--no-admin-check', action='store_true', help='跳过管理员检查')
    parser.add_argument('--batch', '-b', action='store_true', help='批处理模式，禁止交互')
    parser.add_argument('--preview', '-p', action='store_true', help='预览模式，不实际设置')
    parser.add_argument('--idea', action='store_true', help='IDEA启动模式，输出简化')
    parser.add_argument('--verbose', action='store_true', help='详细输出')

    args = parser.parse_args()

    # IDEA模式输出简化
    if args.idea:
        print("=== IDEA模式 ===")

    # 默认配置文件为 env_config.json
    if not args.file:
        default_file = 'env_config.json'
        if Path(default_file).exists():
            args.file = default_file
            if not args.idea and not args.verbose:
                print(f"默认使用配置文件: {default_file}")
        else:
            # 兼容旧版本
            possible_files = ['config.json', 'claude_config.json', 'env.json']
            for f in possible_files:
                if Path(f).exists():
                    args.file = f
                    if not args.idea and not args.verbose:
                        print(f"自动检测到配置文件: {f}")
                    break

    if not args.file:
        print("❌ 请指定配置文件路径 (-f) 或创建 env_config.json")
        sys.exit(1)

    # 列表模式
    if args.list:
        list_configs(args.file, verbose=True)
        sys.exit(0)

    # 确定配置名称
    config_name = parse_config_name(args.config)
    is_user = args.user

    if not config_name:
        config_name, is_user = interactive_mode({}, args.batch)  # 传空字典避免重复加载

    if config_name is None:
        sys.exit(1)

    # 使用方法调用接口执行
    success = set_config(
        config_name=config_name,
        config_file=args.file,
        user_level=is_user,
        preview=args.preview,
        verbose=not args.idea or args.verbose
    )

    if args.idea and not args.verbose:
        if success:
            print(f"\n✅ IDEA执行成功: {config_name}")
        else:
            print(f"\n❌ IDEA执行失败: {config_name}")

    sys.exit(0 if success else 1)

# 公共API
__all__ = ['set_config', 'list_configs', 'set_env_variables', 'load_configurations']

if __name__ == "__main__":
    main()
