import yaml
import logging
from modules.auth import Auth
from modules.maze import MazeBot
from modules.daily import DailyTasks
from modules.profile import ProfileManager
from utils.human_like import HumanBehavior

class NeboBot:
    def __init__(self, config_path):
        """
        Инициализация бота
        
        Args:
            config_path (str): Путь к файлу конфигурации
        """
        self.config = self._load_config(config_path)
        self.session = None
        self.human = HumanBehavior(
            min_delay=self.config['delays']['min'],
            max_delay=self.config['delays']['max']
        )
        
        # Инициализация модулей
        self.auth = Auth(
            self.config['credentials']['login'],
            self.config['credentials']['password']
        )
        
        # Модули будут инициализированы после авторизации
        self.maze_bot = None
        self.daily_tasks = None
        self.profile = None
        
        # Настройка логирования
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger('NeboBot')

    def _load_config(self, config_path):
        """Загрузка конфигурации из YAML файла"""
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)

    def start(self):
        """Запуск бота"""
        try:
            # Авторизация
            self.session = self.auth.login()
            self.logger.info("Успешная авторизация")
            
            # Инициализация модулей с активной сессией
            if self.config['features']['maze']:
                self.maze_bot = MazeBot(self.session, self.human)
            
            if self.config['features']['daily_tasks']:
                self.daily_tasks = DailyTasks(self.session, self.human)
            
            if self.config['features']['profile_upgrade']:
                self.profile = ProfileManager(self.session, self.human)
            
            # Основной цикл работы
            while True:
                self._process_daily_routine()
                self.human.sleep_random()  # Случайная пауза между циклами
                
        except Exception as e:
            self.logger.error(f"Ошибка в работе бота: {e}")
            raise

    def _process_daily_routine(self):
        """Выполнение ежедневных задач"""
        try:
            # Проверка и выполнение ежедневных заданий
            if self.daily_tasks:
                self.daily_tasks.process_tasks()
            
            # Прохождение лабиринта
            if self.maze_bot:
                self.maze_bot.solve_maze()
            
            # Управление профилем
            if self.profile:
                self.profile.check_and_upgrade()
                
        except Exception as e:
            self.logger.error(f"Ошибка в ежедневной рутине: {e}")
            # Продолжаем работу, несмотря на ошибки в отдельных модулях

if __name__ == "__main__":
    bot = NeboBot('config/config.yml')
    bot.start()