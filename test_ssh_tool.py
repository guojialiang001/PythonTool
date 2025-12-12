#!/usr/bin/env python3
"""
SSH连接工具测试脚本
用于测试FastAPI SSH工具的基本功能
"""

import requests
import json
import time
import sys

def test_ssh_tool():
    """测试SSH工具的基本功能"""
    
    # 服务器配置
    BASE_URL = "http://localhost:8000"
    
    # 测试连接信息（需要根据实际情况修改）
    TEST_CONNECTION = {
        "hostname": "127.0.0.1",  # 修改为实际的SSH服务器地址
        "port": 22,
        "username": "testuser",   # 修改为实际的用户名
        "password": "testpass"    # 修改为实际的密码
    }
    
    print("=== SSH连接工具测试 ===")
    print(f"测试服务器: {BASE_URL}")
    print(f"目标SSH服务器: {TEST_CONNECTION['hostname']}:{TEST_CONNECTION['port']}")
    print()
    
    try:
        # 1. 测试API根路径
        print("1. 测试API根路径...")
        response = requests.get(BASE_URL)
        if response.status_code == 200:
            print("✓ API服务正常")
            print(f"   响应: {response.json()}")
        else:
            print("✗ API服务异常")
            return False
        print()
        
        # 2. 测试连接建立
        print("2. 测试SSH连接建立...")
        try:
            response = requests.post(f"{BASE_URL}/ssh/connect", json=TEST_CONNECTION)
            if response.status_code == 200:
                print("✓ SSH连接建立成功")
                print(f"   响应: {response.json()}")
            else:
                print(f"✗ SSH连接建立失败: {response.status_code}")
                print(f"   错误: {response.text}")
                # 连接失败时跳过后续测试
                return False
        except requests.exceptions.ConnectionError:
            print("✗ 无法连接到API服务器，请确保服务已启动")
            return False
        except Exception as e:
            print(f"✗ 连接测试异常: {e}")
            return False
        print()
        
        # 3. 测试命令执行
        print("3. 测试SSH命令执行...")
        command_request = {
            "connection": TEST_CONNECTION,
            "command": "echo 'Hello SSH Tool' && pwd && whoami",
            "timeout": 10
        }
        
        response = requests.post(f"{BASE_URL}/ssh/execute", json=command_request)
        result = response.json()
        
        if response.status_code == 200:
            print("✓ 命令执行完成")
            print(f"   成功: {result['success']}")
            print(f"   退出码: {result.get('exit_code', 'N/A')}")
            print(f"   执行时间: {result['execution_time']:.3f}秒")
            print(f"   输出: {result['output'].strip()}")
            if result['error']:
                print(f"   错误: {result['error'].strip()}")
        else:
            print(f"✗ 命令执行失败: {response.status_code}")
            print(f"   错误: {response.text}")
        print()
        
        # 4. 测试获取连接列表
        print("4. 测试获取连接列表...")
        response = requests.get(f"{BASE_URL}/ssh/connections")
        if response.status_code == 200:
            connections = response.json()
            print("✓ 连接列表获取成功")
            print(f"   活跃连接数: {connections['count']}")
            for conn in connections['connections']:
                print(f"   连接: {conn['connection']}")
        else:
            print(f"✗ 连接列表获取失败: {response.status_code}")
        print()
        
        # 5. 测试断开连接
        print("5. 测试断开SSH连接...")
        response = requests.post(f"{BASE_URL}/ssh/disconnect", json=TEST_CONNECTION)
        if response.status_code == 200:
            print("✓ SSH连接断开成功")
            print(f"   响应: {response.json()}")
        else:
            print(f"✗ SSH连接断开失败: {response.status_code}")
        print()
        
        # 6. 验证连接已断开
        print("6. 验证连接已断开...")
        response = requests.get(f"{BASE_URL}/ssh/connections")
        if response.status_code == 200:
            connections = response.json()
            if connections['count'] == 0:
                print("✓ 连接已成功断开")
            else:
                print("✗ 连接断开验证失败")
        print()
        
        print("=== 测试完成 ===")
        return True
        
    except Exception as e:
        print(f"测试过程中出现异常: {e}")
        return False

def quick_test():
    """快速测试API服务是否可用"""
    try:
        response = requests.get("http://localhost:8000", timeout=5)
        if response.status_code == 200:
            print("✓ SSH工具API服务运行正常")
            return True
        else:
            print("✗ SSH工具API服务异常")
            return False
    except requests.exceptions.ConnectionError:
        print("✗ SSH工具API服务未启动")
        return False
    except Exception as e:
        print(f"✗ 测试异常: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "quick":
        # 快速测试模式
        quick_test()
    else:
        # 完整测试模式
        print("提示: 请先修改测试脚本中的SSH连接信息")
        print("      然后启动SSH工具服务: python ssh_tool.py")
        print("      最后运行此测试脚本")
        print("-" * 50)
        
        # 先检查服务是否运行
        if not quick_test():
            print("\n请先启动SSH工具服务:")
            print("  python ssh_tool.py")
            print("或")
            print("  uvicorn ssh_tool:app --host 0.0.0.0 --port 8000 --reload")
            sys.exit(1)
        
        # 运行完整测试
        success = test_ssh_tool()
        
        if success:
            print("🎉 所有测试通过！")
        else:
            print("❌ 部分测试失败，请检查配置和服务状态")
            sys.exit(1)