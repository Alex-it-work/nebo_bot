import requests
from bs4 import BeautifulSoup
import random
import time

class MazeBot:
    def __init__(self, session, human):
        self.session = session
        self.human = human
        self.base_url = "https://nebo.mobi/doors" 