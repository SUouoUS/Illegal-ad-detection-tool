"""Generate the OCR test images (run once; the PNGs are committed so tests need no fonts).

All codes and handles are fake test data (TEST77, @test_demo ...).
usage: python testsites/ocr/make_images.py   (needs Malgun Gothic, i.e. Windows)
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent / "img"
FONT = "C:/Windows/Fonts/malgunbd.ttf"


def draw(name, items, size=(720, 220), bg=(255, 255, 255)):
    """items: (x, y, text, font size, colour)"""
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    for x, y, text, fs, fg in items:
        d.text((x, y), text, font=ImageFont.truetype(FONT, fs), fill=fg)
    im.save(OUT / name, optimize=True)


def lines(name, texts, fs=36, fg=(20, 20, 20), size=(720, 220), bg=(255, 255, 255)):
    draw(name, [(24, 24 + int(i * fs * 1.45), t, fs, fg) for i, t in enumerate(texts)], size, bg)


def main():
    OUT.mkdir(exist_ok=True)
    k = (20, 20, 20)
    lines("ad_ko.png", ["카지노 첫충 30% 지급", "가입코드 TEST77"], bg=(255, 244, 214))
    lines("ad_en.png", ["ONLINE CASINO BONUS 100%", "CODE: DEMO01"], fg=(255, 255, 255), bg=(24, 40, 90))
    lines("insurance.png", ["실손보험 가입 이벤트", "보험료 10% 할인 상담 신청"], bg=(232, 244, 255))
    lines("ai_service.png", ["AI 비서 서비스 출시", "지금 무료 체험하고 혜택 받으세요"])
    lines("game_news.png", ["게임사, 50억 원 규모 현금배당 예고", "결승 진출 선수에게 포상 휴가 지급"], fs=30)
    lines("prevention.png", ["불법 도박은 범죄입니다", "도박문제 상담 1336"], bg=(240, 248, 240))
    lines("small_text.png", ["카지노 첫충 30% 가입코드 TEST77"], fs=13, size=(360, 60))
    lines("low_contrast.png", ["먹튀검증 안전놀이터 가입코드 TEST77"], fg=(212, 212, 212))
    lines("multiline.png", ["안전놀이터 추천", "첫충 20% 매충 10%", "텔레그램 @test_demo"], fs=32, size=(720, 220))
    draw("separated.png", [(24, 20, "온라인 카지노", 40, k), (430, 150, "오늘의 운세", 26, k),
                           (380, 330, "가입코드 TEST77", 26, k)], size=(720, 400))
    lines("jamo.png", ["ㅋㅏㅈㅣㄴㅗ 가입코드 TEST77"])
    lines("fullwidth.png", ["ｃａｓｉｎｏ 첫가입 30%"])
    draw("big.png", [(300, 1200, "카지노 가입코드 TEST77", 220, k)], size=(6000, 3000))
    lines("lazy_ad.png", ["바카라 무료체험 가입코드 TEST88"], bg=(255, 236, 236))
    lines("ad_2x.png", ["토토사이트 첫충 매충 가입코드 TEST99"], fs=56, size=(1400, 200))
    lines("small.png", ["토토 미리보기"], fs=16, size=(300, 60))
    lines("hidden_ad.png", ["출장안마 24시 후불 문의 텔레 @test_demo"])
    Image.new("RGB", (1, 1), (255, 255, 255)).save(OUT / "placeholder.gif")
    # sub page / iframe / nested iframe cases (collection-range tests)
    lines("s_ad.png", ["홀덤 토너먼트 참가 즉시", "첫충 40% 가입코드 SUB11"], bg=(235, 245, 255))
    lines("s_normal.png", ["가을 걷기 행사 참가자 모집", "접수 기간 10월 1일~15일"], bg=(240, 250, 235))
    lines("s_lazy_ad.png", ["안전놀이터 추천 코드 LAZY5", "텔레그램 @sub_demo"], bg=(255, 245, 225))
    lines("f_ad.png", ["파워볼 분석 무료 가입코드 FRM22", "문의 카톡 frame_demo"], bg=(250, 235, 245))
    lines("f_normal.png", ["도서관 휴관일 안내", "매주 월요일은 쉽니다"])
    lines("n_ad.png", ["스포츠토토 첫충 매충 가입코드 NEST3"], bg=(230, 240, 250))
    for i in range(24):                              # for the stop-while-processing test
        lines(f"many_{i:02d}.png", [f"공지 {i + 1}번 안내 문구입니다", "행사 일정은 누리집을 참고하세요"], fs=30)


if __name__ == "__main__":
    main()
