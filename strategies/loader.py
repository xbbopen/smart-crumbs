import importlib, os

def _name_to_class(name: str) -> str:
    parts = name.split("_")
    return "".join(p.capitalize() for p in parts) + "Strategy"

def load_strategy(name: str):
    module = importlib.import_module(f"strategies.{name}")
    cls = getattr(module, _name_to_class(name), None)
    if cls is None:
        raise ValueError(f"策略 {name} 中未找到类 {_name_to_class(name)}")
    return cls()

def list_available_strategies():
    d = os.path.dirname(__file__)
    return [f[:-3] for f in os.listdir(d) if f.endswith(".py") and f not in ("__init__.py", "base.py", "loader.py")]
