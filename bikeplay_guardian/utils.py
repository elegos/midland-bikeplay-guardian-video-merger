def progress_bar(current: int, total: int, bar_length: int = 40) -> None:
    ''' Display a progress bar in the console.'''
    fraction = current / total
    filled_length = int(bar_length * fraction)
    bar = '█' * filled_length + '-' * (bar_length - filled_length)
    percent = fraction * 100
    print(f'\r|{bar}| {percent:.1f}%', end='\r')
    if current == total:
        print()
