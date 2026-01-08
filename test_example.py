#!/usr/bin/env python3
"""
测试PCA的示例脚本
"""
import time

def test_function_1():
    """测试函数1"""
    print("Running test_function_1")
    return 1 + 1

def test_function_2():
    """测试函数2"""
    print("Running test_function_2")
    result = 0
    for i in range(10):
        result += i
    return result

def main():
    """主函数"""
    print("Starting test application...")
    
    # 执行一些函数
    result1 = test_function_1()
    print(f"test_function_1 result: {result1}")
    
    time.sleep(1)
    
    result2 = test_function_2()
    print(f"test_function_2 result: {result2}")
    
    # 保持运行一段时间，让PCA有机会采集覆盖率
    print("Keeping application running for 70 seconds to test PCA...")
    time.sleep(70)
    
    print("Test application finished")

if __name__ == '__main__':
    main()

