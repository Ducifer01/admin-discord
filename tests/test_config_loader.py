import unittest
import os
from config_loader import config_manager, TOKEN, PREFIX

class TestConfigLoader(unittest.TestCase):
    def test_env_loaded(self):
        # Garante que variáveis principais existem (TOKEN pode estar vazio em ambiente de teste)
        self.assertIsNotNone(PREFIX)

    def test_global_config(self):
        cfg = config_manager.load_global()
        self.assertIn('bot', cfg)
        self.assertIn('logging', cfg)

    def test_cog_configs(self):
        # Adiciona automod_spam e protect_antibot às verificações básicas
        for cog in ['ban', 'castigo', 'buscarmembro', 'clearchat', 'mutecall', 'automod_chat', 'automod_spam', 'automod_nomention', 'protect_antibot', 'anti_raid']:
            data = config_manager.load_cog(cog)
            self.assertIsInstance(data, dict)

if __name__ == '__main__':
    unittest.main()
