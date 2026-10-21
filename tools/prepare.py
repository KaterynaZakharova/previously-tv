"""Compatibility entrypoint for the database-backed media preparation command."""
import runpy
from pathlib import Path
if __name__=='__main__':runpy.run_path(str(Path(__file__).with_name('prepare_assets.py')),run_name='__main__')
