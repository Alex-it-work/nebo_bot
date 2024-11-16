import requests
import logging
from typing import Optional, Dict, Any

class Auth:
    def __init__(self, base_url: str = "https://nebo.mobi"):
        self.base_url = base_url
        self.session = requests.Session()
        self.logged_in = False
        self.cookies: Dict[str, Any] = {}
        
        # Настройка логгера
        self.logger = logging.getLogger(__name__)

    def login(self, username: str, password: str) -> bool:
        """
        Авторизация пользователя в системе
        
        Args:
            username: Имя пользователя
            password: Пароль
            
        Returns:
            bool: True если авторизация успешна, False в противном случае
        """
        try:
            login_url = f"{self.base_url}/login"
            payload = {
                "login": username,
                "password": password
            }
            
            response = self.session.post(login_url, json=payload)
            
            if response.status_code == 200:
                self.cookies = dict(response.cookies)
                self.logged_in = True
                self.logger.info(f"Успешная авторизация пользователя {username}")
                return True
            else:
                self.logger.error(f"Ошибка авторизации: {response.status_code}")
                return False
                
        except Exception as e:
            self.logger.error(f"Ошибка при попытке авторизации: {str(e)}")
            return False

    def get_session(self) -> requests.Session:
        """
        Получить текущую сессию
        
        Returns:
            requests.Session: Объект сессии с установленными cookies
        """
        return self.session

    def is_logged_in(self) -> bool:
        """
        Проверка статуса авторизации
        
        Returns:
            bool: True если пользователь авторизован, False в противном случае
        """
        return self.logged_in

    def get_cookies(self) -> Dict[str, Any]:
        """
        Получить текущие cookies
        
        Returns:
            Dict[str, Any]: Словарь с cookies
        """
        return self.cookies

    def logout(self) -> bool:
        """
        Выход из системы
        
        Returns:
            bool: True если выход успешен, False в противном случае
        """
        try:
            self.session.cookies.clear()
            self.cookies = {}
            self.logged_in = False
            return True
        except Exception as e:
            self.logger.error(f"Ошибка при попытке выхода: {str(e)}")
            return False