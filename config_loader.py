import os
import json
from pathlib import Path
from typing import Any, Dict, Optional
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
CONFIG_DIR = BASE_DIR / 'config'
COGS_CONFIG_DIR = CONFIG_DIR / 'cogs'

ENV_LOADED = False

def load_env(env_file: str = '.env') -> None:
    global ENV_LOADED
    if not ENV_LOADED:
        load_dotenv(env_file)
        ENV_LOADED = True

class ConfigError(Exception):
    pass

class ConfigManager:
    def __init__(self):
        load_env()
        self._global_cache: Optional[Dict[str, Any]] = None
        self._cog_cache: Dict[str, Dict[str, Any]] = {}

    def global_config_path(self) -> Path:
        return CONFIG_DIR / 'global.json'

    def cog_config_path(self, cog_name: str) -> Path:
        return COGS_CONFIG_DIR / f'{cog_name}.json'

    def load_global(self) -> Dict[str, Any]:
        if self._global_cache is None:
            path = self.global_config_path()
            if not path.exists():
                raise ConfigError(f'Arquivo global.json não encontrado em {path}')
            with path.open('r', encoding='utf-8') as f:
                self._global_cache = json.load(f)
        return self._global_cache

    def load_cog(self, cog_name: str, defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if cog_name in self._cog_cache:
            return self._cog_cache[cog_name]
        path = self.cog_config_path(cog_name)
        if not path.exists():
            if defaults is None:
                raise ConfigError(f'Config da cog {cog_name} não encontrada e nenhum default fornecido.')
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('w', encoding='utf-8') as f:
                json.dump(defaults, f, indent=2, ensure_ascii=False)
        with path.open('r', encoding='utf-8') as f:
            data = json.load(f)
        self._cog_cache[cog_name] = data
        return data

    def reload_cog(self, cog_name: str) -> Dict[str, Any]:
        if cog_name in self._cog_cache:
            del self._cog_cache[cog_name]
        return self.load_cog(cog_name)

config_manager = ConfigManager()

TOKEN = os.getenv('DISCORD_TOKEN')
PREFIX = os.getenv('BOT_PREFIX', '!')
OWNER_ID = os.getenv('OWNER_ID')
GUILD_ID = os.getenv('GUILD_ID')

