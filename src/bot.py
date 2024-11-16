from src.modules.auth import Auth
import logging
import sys

class NeboBot:
    """
    Main bot class for Nebo.mobi game.
    Currently handles authentication, with more features to come.
    """
    
    def __init__(self, config_path: str):
        """
        Initialize bot instance.

        Args:
            config_path: Path to configuration file
        """
        self._setup_logger()
        self.logger = logging.getLogger(__name__)
        
        try:
            self.auth = Auth(config_path)
            self.logger.info("Bot initialized successfully")
        except Exception as e:
            self.logger.error(f"Initialization failed: {e}")
            sys.exit(1)

    def _setup_logger(self):
        """
        Configure logging settings
        """
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(sys.stdout)
            ]
        )

    def start(self):
        """
        Start bot operation.
        
        Returns:
            bool: True if login successful, False otherwise
        """
        self.logger.info("Starting bot operation")
        
        if not self.auth.login():
            self.logger.error("Authentication failed")
            return False
            
        self.logger.info("Successfully authenticated")
        return True

    def stop(self):
        """
        Stop bot operation and cleanup
        """
        self.logger.info("Stopping bot operation")
        
        if self.auth.logout():
            self.logger.info("Successfully logged out")
        else:
            self.logger.warning("Logout failed")