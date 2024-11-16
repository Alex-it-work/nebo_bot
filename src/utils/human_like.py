import random
import time

class HumanBehavior:
    def __init__(self, min_delay=1.5, max_delay=3.5):
        """
        Инициализация параметров человеческого поведения
        
        Args:
            min_delay (float): Минимальная задержка между действиями
            max_delay (float): Максимальная задержка между действиями
        """
        self.min_delay = min_delay
        self.max_delay = max_delay
        
    def human_delay(self):
        """Имитация человеческой задержки между действиями"""
        # Базовая задержка
        base_delay = random.uniform(self.min_delay, self.max_delay)
        
        # Иногда делаем длинную паузу
        if random.random() < 0.1:
            base_delay += random.uniform(2, 4)
            
        # Добавляем микро-вариации
        micro_variation = random.uniform(-0.3, 0.3)
        
        return base_delay + micro_variation
    
    def simulate_page_load(self):
        """Имитация времени загрузки страницы"""
        return random.uniform(0.3, 1.2)