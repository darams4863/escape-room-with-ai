import json
from datetime import datetime, timedelta
from typing import List, Dict
from ..core.connections import postgres_manager, redis_manager
from ..core.logger import logger