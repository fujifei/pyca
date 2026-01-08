"""
PYCA CLI工具
"""
import sys
import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [PYCA] - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """CLI入口"""
    parser = argparse.ArgumentParser(description='PYCA (Python Coverage Agent) CLI')
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # status命令
    status_parser = subparsers.add_parser('status', help='Show PYCA status')
    
    # test命令
    test_parser = subparsers.add_parser('test', help='Test PYCA agent')
    
    args = parser.parse_args()
    
    if args.command == 'status':
        show_status()
    elif args.command == 'test':
        test_agent()
    else:
        parser.print_help()


def show_status():
    """显示PYCA状态"""
    import os
    from pathlib import Path
    
    print("PYCA (Python Coverage Agent) Status")
    print("=" * 50)
    
    # 检查环境变量（支持PYCA_*和PCA_*向后兼容）
    enabled = os.getenv('PYCA_ENABLED') or os.getenv('PCA_ENABLED', '1')
    rabbitmq_url = os.getenv('PYCA_RABBITMQ_URL') or os.getenv('PCA_RABBITMQ_URL', '')
    
    print(f"Enabled: {enabled}")
    print(f"RabbitMQ URL: {rabbitmq_url if rabbitmq_url else '(not configured)'}")
    
    # 检查fingerprint文件
    fingerprint_file = Path.home() / '.pyca_fingerprint'
    if fingerprint_file.exists():
        print(f"Fingerprint file: {fingerprint_file} (exists)")
        with open(fingerprint_file, 'r') as f:
            fingerprint = f.read().strip()
            print(f"Last fingerprint: {fingerprint[:16]}...")
    else:
        print(f"Fingerprint file: {fingerprint_file} (not found)")


def test_agent():
    """测试agent"""
    print("Testing PYCA Agent...")
    try:
        from pyca.agent import CoverageAgent
        
        agent = CoverageAgent()
        print("Agent created successfully")
        
        # 测试覆盖率采集
        agent._flush_coverage()
        print("Coverage flush test completed")
        
        agent.stop()
        print("Agent stopped successfully")
        
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

