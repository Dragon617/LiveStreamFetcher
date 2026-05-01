from mitmproxy_rs import local
import inspect

# 检查 unavailable_reason
reason = local.LocalRedirector.unavailable_reason()
print(f'unavailable_reason: {repr(reason)}')

print(f'\ntype: {type(local.LocalRedirector)}')
try:
    print(f'bases: {local.LocalRedirector.__bases__}')
except:
    pass

# 列出所有方法和签名
for name in sorted(dir(local.LocalRedirector)):
    obj = getattr(local.LocalRedirector, name)
    if callable(obj):
        try:
            sig = inspect.signature(obj)
            print(f'  {name}: {sig}')
        except:
            print(f'  {name}: (no sig)')

# 尝试创建实例看看
print('\n--- trying to instantiate ---')
try:
    r = local.LocalRedirector()
    print(f'  instance created: {type(r)}')
    print(f'  instance methods: {[m for m in dir(r) if not m.startswith("_")]}')
except Exception as e:
    print(f'  instantiate failed: {e}')
