class Position:
    def __init__(self):
        self.__position_events = []

    @property
    def position_events(self):
        return self.__position_events

    @position_events.setter
    def position_events(self, new_value):
        self.__position_events.append(new_value)


# if __name__ == '__main__':
#     new_position = Position()
#     print(new_position.position_events)
#     new_position.position_events = "New List Value"
#     print(new_position.position_events)
