import sys
import logging
from src.bot import NeboBot

def main():
    # Setup basic logging configuration
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/nebo_bot.log'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    logger = logging.getLogger(__name__)
    
    try:
        # Initialize and start the bot
        bot = NeboBot('config/config.yml')
        
        if not bot.start():
            logger.error("Bot failed to start")
            sys.exit(1)
            
        # TODO: Add main bot loop here
        
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    except Exception as e:
        logger.exception(f"Unexpected error occurred: {e}")
        sys.exit(1)
    finally:
        # Ensure proper cleanup on exit
        bot.stop()
        logger.info("Bot shutdown complete")

if __name__ == "__main__":
    main()