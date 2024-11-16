import yaml
import logging
from modules.auth import Auth
from modules.maze import MazeBot
from modules.daily import DailyTasks
from modules.profile import ProfileManager
from utils.human_like import HumanBehavior

class NeboBot:
    def __init__(self, config_path):
        self.config = self._load_config(config_path)
        self.session = None
        self.human = HumanBehavior(
            min_delay=self.config['delays']['min'],
            max_delay=self.config['delays']['max']
        )