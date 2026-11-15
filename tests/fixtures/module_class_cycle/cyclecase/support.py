class Fault(Exception):
    pass


class Cell:
    def __init__(self, value):
        self.value = value


from .reader import observed


class Later:
    pass
