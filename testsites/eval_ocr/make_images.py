"""OCR evaluation images (separate from the four-technique evaluation). Fake data only.
usage: python testsites/eval_ocr/make_images.py  (Windows, Malgun Gothic)"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "img"
F = "C:/Windows/Fonts/malgunbd.ttf"


def draw(name, items, size=(760, 240), bg=(255, 255, 255)):
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    for x, y, t, fs, fg in items:
        d.text((x, y), t, font=ImageFont.truetype(F, fs), fill=fg)
    im.save(OUT / name, optimize=True)


def two(name, a, b, fg=(25, 25, 25), bg=(255, 255, 255), fs=38, size=(760, 200)):
    draw(name, [(28, 28, a, fs, fg), (28, 28 + int(fs * 1.5), b, fs, fg)], size, bg)


def main():
    OUT.mkdir(exist_ok=True)
    w = (255, 255, 255)
    two("e_ad1.png", "메이저 토토 입장 즉시", "첫충 25% 코드 JADE3", fg=w, bg=(16, 80, 60))
    two("e_ad2_en.png", "LIVE BACCARAT", "SIGN UP BONUS 50%", fg=(250, 220, 90), bg=(40, 10, 60))
    two("e_loan.png", "무직자 당일 대출", "상담 카톡 fund_demo", bg=(255, 240, 200))
    two("e_insure.png", "치아보험 가입하고", "스케일링 무료 혜택 받으세요", bg=(225, 240, 255))
    two("e_museum.png", "한빛박물관 가을 특별전", "관람료 무료 · 사전 예약", bg=(245, 238, 225))
    two("e_health.png", "금연 클리닉 무료 상담", "가까운 보건소에 방문 예약하세요", bg=(230, 248, 235))
    two("e_prevent.png", "불법 도박 광고를 보면 신고하세요", "신고 112 · 도박문제 상담 1336", fs=32, bg=(250, 245, 230))
    two("e_news.png", "e스포츠 결승 배당 논란", "구단, 선수단에 격려금 지급", fs=34)
    draw("e_split.png", [(28, 20, "파워볼", 44, (25, 25, 25)), (420, 170, "날씨 맑음", 26, (25, 25, 25)),
                         (430, 360, "코드 LUCK5", 26, (25, 25, 25))], size=(760, 420))
    draw("e_topic.png", [(40, 60, "슬롯머신", 60, (180, 20, 20))], size=(500, 200))
    two("e_small.png", "안전놀이터 가입 첫충 코드 JADE3", "", fs=13, size=(340, 50))
    two("e_lowc.png", "먹튀검증 완료 메이저 추천", "텔레 @jade_demo", fg=(214, 214, 214))
    two("e_jamo.png", "ㅌㅗㅌㅗ ㅅㅏㅇㅣㅌㅡ 첫충", "입장 문의 환영")
    two("e_full.png", "ＴＯＴＯ 첫가입 20%", "지금 바로 입장")


if __name__ == "__main__":
    main()
