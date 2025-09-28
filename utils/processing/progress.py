
class WebSocketStats:
    def __init__(self, ws_counter):
        self.ws_counter = ws_counter

    def inc(self):
        self.ws_counter.value += 1

    def dec(self):
        if self.ws_counter.value > 0:
            self.ws_counter.value -= 1

    def count(self):
        return self.ws_counter.value



class Progress:
    def __init__(self, total: int):
        self.processed = 0
        self.total = total

    def increment(self):
        self.processed += 1

    def reset(self):
        self.processed = 0
