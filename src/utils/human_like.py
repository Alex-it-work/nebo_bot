import random

class HumanBehavior:
    """
    A class that simulates human-like behavior in automated interactions.
    
    This class provides methods to generate realistic delays and timing variations
    that mimic human behavior when interacting with applications or websites.
    It helps make automated actions appear more natural by avoiding constant
    or predictable timing patterns.
    
    Attributes:
        min_delay (float): Minimum delay between actions in seconds
        max_delay (float): Maximum delay between actions in seconds
    """
    
    def __init__(self, min_delay=1.5, max_delay=3.5):
        """
        Initialize human behavior parameters
        
        Args:
            min_delay (float): Minimum delay between actions in seconds
            max_delay (float): Maximum delay between actions in seconds
        """
        self.min_delay = min_delay
        self.max_delay = max_delay
        
    def human_delay(self):
        """
        Simulate human-like delay between actions
        
        Returns:
            float: A randomized delay in seconds that mimics human behavior
        """
        # Base delay
        base_delay = random.uniform(self.min_delay, self.max_delay)
        
        # Occasionally add a longer pause
        if random.random() < 0.1:
            base_delay += random.uniform(2, 4)
            
        # Add micro-variations
        micro_variation = random.uniform(-0.3, 0.3)
        
        return base_delay + micro_variation
    
    def simulate_page_load(self):
        """
        Simulate realistic page load waiting time
        
        Returns:
            float: A randomized page load time in seconds
        """
        return random.uniform(0.3, 1.2)