from PIL import ImageDraw, ImageFont

def progress_bar(current: int, total: int, bar_length: int = 40) -> None:
    ''' Display a progress bar in the console.'''
    fraction = current / total
    filled_length = int(bar_length * fraction)
    bar = '█' * filled_length + '-' * (bar_length - filled_length)
    percent = fraction * 100
    print(f'\r|{bar}| {percent:.1f}%', end='\r')
    if current == total:
        print()

def textbox_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[float, float]:
    '''Returns the width and height of the text in pixels'''
    
    text_bbox = draw.textbbox((0, 0), text, font=font)

    return text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]