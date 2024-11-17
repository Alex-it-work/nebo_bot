import requests
import logging
from ..utils.human_like import HumanBehavior
import yaml
import time
from bs4 import BeautifulSoup

class Auth:
    """
    Authentication management class for Nebo.mobi game.
    Handles login, logout and session management functionality.
    """
    
    def __init__(self, config_path):
        """
        Initialize Auth instance.

        Args:
            config_path (str): Path to configuration YAML file
        """
        self.session = requests.Session()
        self.human = HumanBehavior()
        self.load_config(config_path)
        self.logger = logging.getLogger(__name__)

    def load_config(self, config_path):
        """
        Load configuration from YAML file.

        Args:
            config_path (str): Path to configuration file

        Raises:
            Exception: If configuration file cannot be loaded or parsed
        """
        try:
            with open(config_path, 'r') as file:
                config = yaml.safe_load(file)
                self.base_url = config.get('base_url', 'https://nebo.mobi')
                self.username = config.get('username')
                self.password = config.get('password')
                self.timeout = config.get('timeout', 30)
        except Exception as e:
            self.logger.error(f"Failed to load configuration: {e}")
            raise

    def get_form_params(self):
        """
        Get login form parameters including Wicket interface and hidden fields.
        Uses direct form search without relying on specific IDs.

        Returns:
            tuple: (wicket_interface, hidden_field) or (None, None) if parsing fails
        """
        try:
            response = self.session.get(
                f"{self.base_url}/login",
                timeout=self.timeout
            )
            
            soup = BeautifulSoup(response.text, 'html.parser')
            # Ищем форму по её методу и наличию loginForm в action
            form = soup.find('form', attrs={
                'method': 'post',
                'action': lambda x: x and 'loginForm' in x
            })
            
            if not form:
                self.logger.error("Login form not found")
                return None, None
                
            # Получаем wicket:interface из action формы
            action = form.get('action', '')
            wicket_interface = action.split('wicket:interface=')[-1]
            
            # Ищем hidden input внутри этой формы
            hidden_input = form.find('input', {'type': 'hidden'})
            hidden_field_name = hidden_input.get('name') if hidden_input else None

            return wicket_interface, hidden_field_name
            
        except Exception as e:
            self.logger.error(f"Failed to get form parameters: {e}")
            return None, None

    def login(self):
        """
        Authenticate user in the system.
        Simulates human-like behavior with delays between actions.

        Returns:
            bool: True if login successful, False otherwise
        """
        try:
            time.sleep(self.human.human_delay())
            
            # Get form parameters
            wicket_interface, hidden_field_name = self.get_form_params()
            if not wicket_interface:
                return False
            
            payload = {
                f'wicket:interface': wicket_interface,  # Используем полученный интерфейс
                hidden_field_name : '',
                'login': self.username,
                'password': self.password,
            }
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            
            # Simulate page reading delay
            time.sleep(self.human.simulate_page_load())
            
            # Perform login request
            login_response = self.session.post(
                f"{self.base_url}/login",
                data=payload,
                headers=headers,
                timeout=self.timeout
            )
            
            # Check if login cookie is present in response cookies
            if login_response.status_code == 200 and 'login' in self.session.cookies:
                self.logger.info("Successfully authenticated")
                return True
            else:
                self.logger.error("Authentication failed")
                return False
                
        except requests.RequestException as e:
            self.logger.error(f"Login request failed: {e}")
            return False

    def logout(self):
        """
        Logout user from the system.
        Clears session cookies after successful logout.

        Returns:
            bool: True if logout successful, False otherwise
        """
        try:
            time.sleep(self.human.human_delay())
            
            response = self.session.get(
                f"{self.base_url}/logout",
                timeout=self.timeout
            )
            
            # Check if login cookie was removed or expired
            login_cookie = self.session.cookies.get('login')
            if response.status_code == 200 and (not login_cookie or login_cookie == ""):
                self.logger.info("Successfully logged out")
                self.session.cookies.clear()
                return True
            else:
                self.logger.error("Logout failed")
                return False
                
        except requests.RequestException as e:
            self.logger.error(f"Logout request failed: {e}")
            return False

    def is_authenticated(self):
        """
        Check if current session is authenticated by verifying required cookies.

        Returns:
            bool: True if user is authenticated, False otherwise
        """
        try:
            # Check if all required cookies are present
            cookies = self.session.cookies
            required_cookies = ['JSESSIONID', 'id', 'login']
            
            if not all(cookie in cookies for cookie in required_cookies):
                return False
                
            # Additional check by requesting profile page
            response = self.session.get(
                f"{self.base_url}/profile",
                timeout=self.timeout
            )
            
            return response.status_code == 200 and 'login' not in response.url
            
        except:
            return False