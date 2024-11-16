import requests
import logging
from ..utils.human_like import HumanBehavior
import yaml
import time

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

    def login(self):
        """
        Authenticate user in the system.
        Simulates human-like behavior with delays between actions.

        Returns:
            bool: True if login successful, False otherwise
        """
        try:
            time.sleep(self.human.human_delay())
            
            payload = {
                'username': self.username,
                'password': self.password,
                'action': 'login'
            }
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
            }
            
            # Initial request to get cookies and tokens
            response = self.session.get(
                f"{self.base_url}/login",
                headers=headers,
                timeout=self.timeout
            )
            
            # Simulate page reading delay
            time.sleep(self.human.simulate_page_load())
            
            # Perform login request
            login_response = self.session.post(
                f"{self.base_url}/login",
                data=payload,
                headers=headers,
                timeout=self.timeout
            )
            
            if login_response.status_code == 200 and 'welcome' in login_response.text.lower():
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
            
            if response.status_code == 200:
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
        Check if current session is authenticated.

        Returns:
            bool: True if user is authenticated, False otherwise
        """
        try:
            response = self.session.get(
                f"{self.base_url}/profile",
                timeout=self.timeout
            )
            return response.status_code == 200 and 'login' not in response.url
        except:
            return False