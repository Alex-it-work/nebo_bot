import requests
from bs4 import BeautifulSoup
import random
import time

class MazeBot:
    def __init__(self, auth_instance):
        """
        Инициализация бота для прохождения лабиринта
        
        Args:
            auth_instance: Экземпляр класса Auth с готовой сессией
        """
        self.session = auth_instance.session
        self.human = auth_instance.human
        self.base_url = auth_instance.base_url  # И базовый URL возьмём оттуда же
        
    def get_current_level(self, html):
        """Extract current level from page HTML"""
        soup = BeautifulSoup(html, 'html.parser')
        amount = soup.find('b', class_='amount')
        return int(amount.text) if amount else 0
        
    def is_dead_end(self, html):
        """Check if we hit a dead end"""
        soup = BeautifulSoup(html, 'html.parser')
        notify = soup.find('span', class_='notify')
        return notify is not None and "тупик" in notify.text
        
    def get_door_links(self, html):
        """Extract door links and their wicket interfaces from page HTML"""
        soup = BeautifulSoup(html, 'html.parser')
        doors = []
        
        # Ищем все ссылки с wicket:interface для дверей
        door_links = soup.find_all('a', href=lambda x: x and 'wicket:interface' in x and 'doorLink' in x)
        
        for link in door_links:
            # Вытаскиваем wicket:interface из href
            wicket_interface = link['href'].split('wicket:interface=')[-1]
            doors.append(wicket_interface)
        
        return doors
    
    def wait_for_page_load(self, response):
        """
        Проверяем что страница обновилась после действия
        """
        html = response.text
        
        # Если попали в тупик - страница обновилась
        if self.is_dead_end(html):
            return True
            
        # Если изменился уровень - страница обновилась    
        current_level = self.get_current_level(html)
        if current_level > 0:  # Любой положительный уровень означает что страница загрузилась
            return True
            
        return False
    
    def solve_maze(self):
        """Main logic for solving the maze"""
        attempts = 0
        while True:
            attempts += 1
            print(f"\nПопытка #{attempts}")
            current_level = 0
            
            print("Загружаем начальную страницу...")
            response = self.session.get(self.base_url)
            time.sleep(self.human.simulate_page_load())
            
            while current_level < 10:
                html = response.text
                current_level = self.get_current_level(html)
                print(f"Текущий уровень: {current_level}")
                
                if self.is_dead_end(html):
                    print("Упс, тупик! Начинаем заново...")
                    time.sleep(self.human.human_delay() * 1.5)  # Делаем паузу подольше после неудачи
                    response = self.session.get(self.base_url)
                    time.sleep(self.human.simulate_page_load())
                    break
                
                door_links = self.get_door_links(html)
                if not door_links:
                    print("Не нашли дверей. Что-то пошло не так!")
                    break
                
                # "Думаем" перед выбором двери
                time.sleep(self.human.human_delay())
                
                # Выбираем дверь
                chosen_door = random.choice(door_links)
                print("Выбираем дверь...")
                
                try:
                    full_url = chosen_door if chosen_door.startswith('http') else f"{self.base_url}{chosen_door}"
                    response = self.session.get(full_url)
                    # Имитируем загрузку страницы
                    time.sleep(self.human.simulate_page_load())
                except Exception as e:
                    print(f"Ошибка при открытии двери: {e}")
                    time.sleep(self.human.human_delay())  # Пауза перед повторной попыткой
                    break
            
            if current_level >= 10:
                print("Ура! Лабиринт пройден!")
                break