"""Internal entry point for killable Glob/Grep operations."""
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from nonocode.tools.glob import Glob
from nonocode.tools.grep import Grep


def main():
    tool={'Glob':Glob,'Grep':Grep}[sys.argv[1]]()
    params=tool.params_model.model_validate_json(sys.argv[2])
    result=tool.execute_sync(params)
    print(json.dumps({'output':result.output,'is_error':result.is_error},ensure_ascii=False))


if __name__=='__main__':
    main()
