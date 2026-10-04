"""User-provided 2026 lunch history; synthetic ratings are explicitly labelled."""
import hashlib
import re
from collections import defaultdict

AUTHOR = '방문기록 · 예시 리뷰'
KEY = 'lunch_history_20261002_v1'
ALIASES = {'어고집밥':'이물비 어고집밥', '청년밥상':'청년밥상문간 슬로우점',
           '서브웨이':'써브웨이 대학로점', '도도야':'도도야 혜화본점',
           '그리너':'그리너 서울혜화점', '국수가':'국수가 대학로본점',
           '순대실록':'순대실록 대학로본점', '소바의 온도':'소바의온도 본점'}
# Ambiguous March dates remain week labels, never invented visit dates.
RAW = '''
01-02|이화김치찌개|GDa23E8M
01-05|어고집밥|5bVsYOf2
01-06|제로밥상|
01-07|성북동집|FvEg5xbE
01-08|버거파크|FG7xn2E5
01-09|순대실록|5tJtlayi
01-12|하이콴|5eZtkRRw
01-13|아비꼬|5r95rVya
01-14|콩나물장수|5WOkVWrU
01-15|메종 아카이|GV2t5tSR
01-16|엄마손돼지불백|5xnueN7H
01-19|코야코|IDB1o7IR
01-20|한촌설렁탕|x0UEDBC4
01-27|청년밥상|xa52DUZv
01-28|서래향|FdCxyTgr
01-29|서브웨이|
01-30|국수가|5WOQeGyP
02-03|혜화칼국수|GvcQXu09
02-04|또보겠지떡볶이|xKtnSaqh
02-05|파파이스|GwSU6LdU
02-06|서브웨이|xxY2SWkP
02-09|혜화도담|FY3yD5E3
02-10|아비꼬|5r95rVya
02-11|콩나물장수|5WOkVWrU
02-12|풍성뚝배기|xOxXxaAt
02-19|돈텐동식당|xhzCEykg
02-20|가마솥순대국밥 대학로점|FMTckUra
02-23|버거파크|FG7xn2E5
02-24|이화김치찌개|GDa23E8M
02-27|청화원 대학로점|FsRsuBSq
03-03|미락분식|
03-04|포카치아 샌드위치 날에|FUhCxq8S
03-05|충무칼국수|G0DXhvJy
03-06|혜화동 베이커리|
3월 2주|롤링 파스타|
3월 2주|삼삼뼈국|
3월 2주|도도야|
3월 2주|프레퍼스|
3월 3주|엄마손돼지불백|
3월 3주|겐로쿠우동|
3월 3주|카산도|
3월 3주|그리너|
03-23|슬램버거|5M5N5UNE
03-24|긴자료코|Fn6AoVWh
03-25|순대실록|5tJtlayi
03-26|국수가|5WOQeGyP
03-27|서래향|FdCxyTgr
03-30|제순식당|IGJIE6fg
03-31|콩나물장수|
04-01|삼청동수제비|
04-02|프레퍼스|
04-03|성북동누룽지백숙|
04-06|침스버거|FxFtbQy8
04-07|스모키트레인|
04-08|신선식탁|
04-09|콩나물장수|5WOkVWrU
04-10|바오쯔|
04-13|혜화 골목냉면|55rw4oPt
04-14|시올돈 성북직영점|GQ1E4Y5n
04-15|백소정|5D8Iniu5
04-16|지미존스|
04-17|신선식탁|
04-20|커피내리는분식|FHl0WyBl
04-21|미스사이공|Grma0zgO
04-22|청년밥상|xa52DUZv
04-23|순대실록|5tJtlayi
04-27|도도야|
04-28|혜화동버거|
04-29|진아춘|
04-30|육전국밥|
05-04|어고집밥|
05-06|소바의 온도|
05-07|이화동 개성만두|GctJazk1
05-08|에그 놀 씨어터 대학로|
05-11|바오쯔|
05-12|낙산어전|
05-13|육전국밥|
05-14|재즈스파이스|
05-15|죽이야기|
05-18|버거파크|FG7xn2E5
05-19|서래향|FdCxyTgr
05-20|국수가|5WOQeGyP
05-21|이화김치찌개|GDa23E8M
05-22|광산포차|xAA4CAhM
06-15|순대실록|5tJtlayi
06-17|국수가|5WOQeGyP
06-18|이화동 개성만두|GctJazk1
06-19|서래향|FdCxyTgr
07-13|순대실록|5tJtlayi
07-14|이화김치찌개|GDa23E8M
07-15|마당너른집|G8s9IPDP
07-16|엄마손돼지불백|5xnueN7H
'''

def normalized(name):
    return re.sub(r'[\s·()]+', '', name).lower()

def grouped():
    out = defaultdict(list)
    for line in RAW.strip().splitlines():
        date, name, link = line.split('|')
        out[name].append((date, 'https://naver.me/' + link if link else ''))
    return out

def import_history(conn, q, now):
    locations = [dict(r) for r in conn.execute('SELECT * FROM lunch_location').fetchall()]
    locations = [r for r in locations if '이화장길100' in normalized(r.get('address') or '')]
    if len(locations) != 1:
        raise ValueError('점심 기록 대상 위치를 단일하게 확인할 수 없음')
    loc_id = locations[0]['id']
    # The unique marker is acquired in the same transaction as all inserts.
    claimed = conn.execute(q('INSERT INTO meta (key,value) VALUES (?,?) ON CONFLICT (key) DO NOTHING RETURNING key'), (KEY, now)).fetchone()
    if not claimed:
        return 0
    rows = [dict(r) for r in conn.execute(q('SELECT * FROM lunch_restaurant WHERE loc_id=?'), (loc_id,)).fetchall()]
    for name, records in grouped().items():
        target = normalized(ALIASES.get(name, name))
        targets = {normalized(name), target}
        matches = [r for r in rows if normalized(r['name']) in targets]
        if not matches:
            # Only a unique branch-qualified name is accepted; no arbitrary fuzzy match.
            branches = {t+s for t in targets for s in ('대학로점','혜화점','대학로','혜화','대학로본점','혜화본점','본점')}
            matches = [r for r in rows if normalized(r['name']) in branches]
        if len(matches) > 1:
            raise ValueError('중복 식당 확인 필요: ' + name)
        if matches:
            rid = matches[0]['id']
        else:
            link = next((url for _, url in records if url), '')
            row = conn.execute(q('INSERT INTO lunch_restaurant (loc_id,source,place_id,name,cat_norm,place_url,excluded,first_seen,last_checked) VALUES (?,?,?,?,?,?,0,?,?) RETURNING id'),
                               (loc_id,'history','history:'+target,name,'기타',link,now,now)).fetchone()
            rid = row['id']
        # Modest synthetic distribution; frequency is factual, score is not.
        choice = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16) % 10
        rating = 3 if choice < 2 else (5 if choice == 9 else 4)
        count = len(records)
        intro = f'점심 기록에 {count}회 등장.'
        observation = ' 여러 번 찾았던 곳이라 점심 후보로 다시 살펴볼 만해요.' if count > 1 else ' 한 번 방문한 기록이 있어요. 다음 선택 때 메뉴를 다시 확인해 보세요.'
        extra = ' 4월 기록은 배달 이용이에요.' if name == '침스버거' else (' 기록에 점심 한식뷔페 운영으로 메모되어 있어요.' if name == '광산포차' else '')
        comment = '[예시 리뷰 · 임의 평점] '+intro+observation+extra+' 맛·가격·응대에 대한 실제 평가가 아닙니다.'
        conn.execute(q('INSERT INTO lunch_review (restaurant_id,username,rating,comment,created_at) VALUES (?,?,?,?,?)'), (rid,AUTHOR,rating,comment,now))
        for date, _ in records:
            if re.fullmatch(r'\d{2}-\d{2}', date):
                conn.execute(q('INSERT INTO lunch_visit (restaurant_id,username,visited_at) VALUES (?,?,?)'), (rid,'방문기록 가져오기','2026-'+date))
    return len(grouped())


def rewrite_history(conn, q, now):
    """Rewrite only imported placeholder reviews; leave actual user reviews intact."""
    # Upgrade only this import's previously generated author label.
    conn.execute(q("UPDATE lunch_review SET username=? WHERE username=? AND created_at=(SELECT value FROM meta WHERE key=?)"),
                 ('관리자', '휴', KEY))
    marker = KEY + '_personal_notes'
    claimed = conn.execute(q('INSERT INTO meta (key,value) VALUES (?,?) ON CONFLICT (key) DO NOTHING RETURNING key'),
                           (marker, '방문기록 기반 개인 메모; 평점은 사용자 요청으로 임의 부여')).fetchone()
    if not claimed:
        return 0
    rows = conn.execute(q('SELECT v.id,v.comment,r.name FROM lunch_review v JOIN lunch_restaurant r ON r.id=v.restaurant_id WHERE v.username=?'), (AUTHOR,)).fetchall()
    for row in rows:
        old = row['comment'] or ''
        match = re.search(r'점심 기록에 (\d+)회 등장', old)
        if not match:
            raise ValueError('가져온 리뷰의 방문 횟수를 확인할 수 없음')
        count = int(match.group(1))
        tone = int(hashlib.sha256((row['name'] or '').encode()).hexdigest()[:8], 16) % 3
        if '배달 이용' in old:
            comment = '4월에 배달로 한 번 먹었음. 점심 배달 후보로 기록해 둔다.'
        elif '한식뷔페' in old:
            comment = '5월 점심에 한 번 갔음. 당시 기록에는 점심 한식뷔페로 적어 뒀다.'
        elif count >= 4:
            comment = [f'올해 점심으로 {count}번 찾았다. 여러 번 갔던 곳이라 다음 점심 후보에도 남겨 둔다.',
                       f'기록을 보니 점심에 {count}번 갔음. 한동안 자주 찾았던 곳 중 하나.',
                       f'점심으로 {count}번 방문. 메뉴 고민할 때 다시 떠올릴 만한 곳으로 적어 둠.'][tone]
        elif count >= 2:
            comment = [f'점심으로 {count}번 방문. 한 번으로 끝나지 않고 다시 갔던 곳이다.',
                       f'올해 점심 기록에 {count}번 남아 있다. 재방문했던 곳이라 따로 적어 둠.',
                       f'점심에 {count}번 다녀옴. 다음에 근처에서 식사할 때 참고할 곳.'][tone]
        else:
            comment = ['점심으로 한 번 방문. 다녀온 곳을 잊지 않으려고 기록해 둔다.',
                       '한 번 점심 먹으러 갔던 곳. 다음 식사 고를 때 참고하려고 남겨 둠.',
                       '점심 방문 기록이 한 번 있다. 아직 자주 간 곳은 아니라 방문 목록에만 적어 둔다.'][tone]
        conn.execute(q('UPDATE lunch_review SET username=?,comment=? WHERE id=? AND username=?'),
                     ('관리자', comment, row['id'], AUTHOR))
    return len(rows)


PERSONAL_NOTES = {
  "이화김치찌개": "김치찌개로 점심을 정할 때 떠오르는 곳. 1월부터 7월까지 네 번 갔으니 방문 목록에서 빼놓기 어렵다.",
  "어고집밥": "1월에 갔다가 5월에 다시 방문. 밥 한 끼 먹을 곳을 찾을 때 다시 살펴볼 후보.",
  "제로밥상": "연초 점심 목록에 넣었던 곳. 아비꼬와 지도 링크가 겹쳐 있어, 다음에 갈 때는 식당 이름으로 위치를 확인해야겠다.",
  "성북동집": "1월 초에 들렀던 식당. 최근 방문은 아니라 메뉴를 다시 확인하고 가는 편이 좋겠다.",
  "버거파크": "밥 대신 버거로 바꾸고 싶은 날 생각나는 곳. 1·2·5월에 한 번씩 방문했다.",
  "순대실록": "이번 기록에서 가장 자주 간 곳으로 총 다섯 번 방문. 점심 선택지를 길게 고민하고 싶지 않을 때 먼저 떠올릴 만하다.",
  "하이콴": "1월 중순 점심으로 선택했던 곳. 한 번 방문한 기록이라 다음에는 다른 점심 후보와 함께 비교해 볼 생각.",
  "아비꼬": "카레로 점심 메뉴를 정했던 선택지. 1월과 2월에 연달아 방문한 기록이 있다.",
  "콩나물장수": "1월부터 4월까지 네 번 찾았다. 4월에는 국밥으로 적어 둬서, 국밥 생각나는 날 다시 확인하기 좋다.",
  "메종 아카이": "1월 점심 목록 중 이름을 따로 기억해 두고 싶은 곳. 아직 재방문 기록은 없으니 다음에 메뉴부터 다시 살펴보자.",
  "엄마손돼지불백": "돼지불백 먹을 곳을 찾을 때 후보로 두는 식당. 겨울·봄·여름에 걸쳐 세 번 방문했다.",
  "코야코": "1월 하순에 점심 먹으러 갔던 곳. 그 뒤 방문 기록은 없어 오랜만에 다시 갈 후보로 남겨 둠.",
  "한촌설렁탕": "설렁탕으로 점심을 정했던 날의 선택. 국물 있는 식사가 당기면 방문 목록에서 꺼내 볼 곳.",
  "청년밥상": "1월과 4월에 방문. 한동안 안 갔다가 다시 찾은 곳이라 다음 점심 후보에도 넣어 둔다.",
  "서래향": "1월 이후 3·5·6월에도 방문했다. 여러 달에 걸쳐 다시 찾았다는 점이 기록에서 눈에 띈다.",
  "서브웨이": "1월 말에 먹고 일주일 정도 뒤에 다시 선택. 샌드위치로 점심을 바꾸고 싶을 때 생각나는 선택지.",
  "국수가": "1·3·5·6월에 한 번씩, 총 네 번 방문. 면으로 점심을 정할 때 확인할 곳.",
  "혜화칼국수": "2월 첫 주 점심으로 갔던 칼국수집. 버거나 샌드위치 대신 국물 있는 면을 먹고 싶을 때 후보.",
  "또보겠지떡볶이": "2월에 떡볶이로 점심 메뉴를 바꿨던 날. 늘 먹던 밥 메뉴에서 벗어나고 싶을 때 기억해 둘 곳.",
  "파파이스": "2월 점심에 한 번 선택. 같은 날 저녁 회식 기록과는 구분해서 점심 방문만 남긴다.",
  "혜화도담": "2월 둘째 주를 시작하며 방문했던 식당. 이후에는 안 갔으니 다음 방문 전 점심 메뉴를 다시 확인할 예정.",
  "풍성뚝배기": "2월 중순 점심 목록에 있던 곳. 뚝배기 식사가 떠오르면 이 방문 기록부터 다시 살펴보자.",
  "돈텐동식당": "설 연휴가 지난 뒤 점심으로 선택했던 곳. 다른 날과 메뉴를 바꾸고 싶었던 2월의 방문 기록.",
  "가마솥순대국밥 대학로점": "순대국밥으로 점심을 정했던 식당. 순대실록과 함께 국밥 후보로 기억해 두면 고르기 편하겠다.",
  "청화원 대학로점": "2월 마지막 점심 기록. 월말에 방문한 곳으로 남겨 두고, 다음에는 메뉴를 다시 보고 결정.",
  "미락분식": "3월 초 점심을 해결했던 분식집. 분식 쪽으로 메뉴를 좁힐 때 다시 확인할 선택지.",
  "포카치아 샌드위치 날에": "3월 4일 팀 포카치아 샌드위치 날에 먹었던 곳. 그날의 메뉴가 함께 남아 있어 기억하기 쉽다.",
  "혜화동 베이커리": "3월 첫 주 금요일에는 베이커리를 선택. 점심 후보를 밥집으로만 한정하지 않으려고 남긴 기록.",
  "충무칼국수": "3월 초에 다녀온 칼국수집. 혜화칼국수와 방문 시기가 달라 다음에는 두 곳의 메뉴를 비교해 보고 싶다.",
  "롤링 파스타": "3월 둘째 주에는 파스타로 점심 메뉴를 바꿨다. 정확한 날짜는 기록이 겹쳐 주차만 남겨 둠.",
  "삼삼뼈국": "3월 둘째 주 점심 후보에 넣었던 곳. 날짜를 단정하지 않고 그 주에 방문한 식당으로 기억해 둔다.",
  "도도야": "3월에 갔다가 4월 말에 다시 찾았다. 한 달 정도 지나 다시 선택한 점심 후보.",
  "프레퍼스": "3월과 4월에 방문했고, 첫 기록에는 다이어트 푸드로 적혀 있다. 평소 식사와 다른 방향으로 고르고 싶을 때 확인할 곳.",
  "겐로쿠우동": "3월 셋째 주에는 우동을 선택. 국수·칼국수와 다른 면 메뉴가 생각날 때 꺼내 볼 기록.",
  "카산도": "3월 셋째 주 점심 목록에 남아 있다. 한 번 가 본 곳이라 새로운 식당부터 찾기 전에 다시 살펴볼 후보.",
  "그리너": "3월 셋째 주에 들렀던 곳. 날짜가 정확하지 않은 대신 그 주의 다른 점심 선택지와 묶어서 기억해 둔다.",
  "슬램버거": "3월 넷째 주 월요일은 버거로 시작. 버거파크 외에 가 본 버거집을 늘려 둔 기록.",
  "긴자료코": "슬램버거 다음 날 점심으로 선택. 하루씩 메뉴를 바꿔 먹던 3월 말의 방문 목록.",
  "제순식당": "3월 말 아이네가 골라 준 곳. 누가 추천했는지 메모가 남아 있어 다음에 이야기 나올 때 떠올리기 좋다.",
  "삼청동수제비": "4월 첫날에는 수제비를 먹으러 갔다. 국물 있는 점심을 고를 때 면 외에 생각해 볼 선택지.",
  "성북동누룽지백숙": "4월 첫 주 금요일에 선택했던 곳. 누룽지백숙 쪽으로 메뉴를 정할 때 다시 확인할 식당.",
  "침스버거": "4월에는 매장 방문 대신 배달로 먹었다. 점심 배달 후보와 직접 갈 식당을 구분해서 기억해 두기.",
  "스모키트레인": "4월 둘째 주에 처음 기록한 식당. 아직 한 번뿐이라 다음 선택 전에 점심 메뉴를 다시 확인해 보고 싶다.",
  "신선식탁": "4월에 9일 간격으로 두 번 찾았다. 같은 달 안에 다시 선택했던 점심 후보라는 점을 기억해 둠.",
  "바오쯔": "4월에 방문하고 다음 달에도 다시 갔다. 한 달 뒤에 재방문한 곳이라 점심 목록에 계속 남겨 둔다.",
  "혜화 골목냉면": "4월 중순에는 냉면으로 메뉴를 정했다. 국밥이나 따뜻한 면과 다른 점심이 떠오를 때 확인할 곳.",
  "시올돈 성북직영점": "4월 점심으로 성북직영점을 방문. 같은 이름의 다른 지점과 헷갈리지 않도록 지점명까지 적어 둠.",
  "백소정": "4월 중순, 전날과 다른 식당으로 골랐던 곳. 아직 한 번 방문이라 다음에는 메뉴 선택지를 다시 살펴볼 생각.",
  "지미존스": "4월 점심 목록에 서브웨이와 별개로 남긴 선택지. 비슷한 식사 후보를 고를 때 한 곳만 떠올리지 않으려고 기록.",
  "커피내리는분식": "4월 하순 월요일에 다녀온 곳. 분식으로 점심을 고를 때 미락분식과 함께 떠올릴 후보.",
  "미스사이공": "4월에 점심으로 선택한 식당. 다른 날과 메뉴를 바꿔 먹던 주의 방문 목록에 남겨 둠.",
  "혜화동버거": "4월 말에 방문한 버거집. 버거파크·슬램버거와 구분해서 점심 선택지를 정리해 둔다.",
  "진아춘": "4월 마지막 주 수요일에 다녀왔다. 월말 점심 기록을 훑어볼 때 다시 떠올릴 수 있도록 남겨 둠.",
  "육전국밥": "4월 말에 먹고 5월 중순에 다시 방문. 국밥을 고를 때 기존에 가 본 후보로 참고하기 좋다.",
  "소바의 온도": "5월 초에는 소바로 점심 메뉴를 바꿨다. 칼국수·우동 말고 다른 면이 떠오를 때 살펴볼 곳.",
  "이화동 개성만두": "5월과 6월에 한 번씩 방문. 만두 메뉴가 생각날 때 점심 후보에서 찾기 쉽게 남겨 둔다.",
  "에그 놀 씨어터 대학로": "5월 첫 주 금요일에 갔던 곳. 식당 이름과 대학로 표기를 함께 남겨 다음에 찾을 때 헷갈리지 않게 기록.",
  "낙산어전": "5월 둘째 주 화요일에 방문. 아직 다른 방문 기록은 없어 한 번 가 본 점심 후보로 정리해 둠.",
  "재즈스파이스": "5월 중순 점심으로 선택했던 곳. 이름이 기억에 남아 다음에 메뉴를 다시 찾아볼 후보로 남긴다.",
  "죽이야기": "5월 금요일 점심에는 죽집을 선택. 식사 메뉴를 바꾸고 싶을 때 일반 밥집 외에 떠올릴 수 있는 곳.",
  "광산포차": "5월 방문 당시에는 점심 한식뷔페 운영으로 적어 뒀다. 저녁 술집 이름만 보고 점심 후보에서 빼지 않으려고 남긴 메모.",
  "마당너른집": "7월 중순 이화김치찌개 다음 날 방문. 이번 기록의 마지막 주에 새로 넣었던 점심 식당."
}


def rewrite_varied_history(conn, q):
    """Refresh this import only, with a distinct note for every restaurant."""
    marker = KEY + "_varied_notes"
    claimed = conn.execute(q("INSERT INTO meta (key,value) VALUES (?,?) ON CONFLICT (key) DO NOTHING RETURNING key"), (marker, "식당별 방문기록 개인 메모")).fetchone()
    if not claimed:
        return 0
    rows = conn.execute(q("SELECT v.id,r.name FROM lunch_review v JOIN lunch_restaurant r ON r.id=v.restaurant_id WHERE v.username=? AND v.created_at=(SELECT value FROM meta WHERE key=?)"), ("관리자", KEY)).fetchall()
    mapping = {}
    for name, note in PERSONAL_NOTES.items():
        targets = {normalized(name), normalized(ALIASES.get(name,name))}
        for target in targets:
            for suffix in ("","대학로점","혜화점","대학로","혜화","대학로본점","혜화본점","본점"):
                mapping[target+suffix] = note
    if len(rows) != len(PERSONAL_NOTES):
        raise ValueError("가져온 리뷰 개수가 일치하지 않음")
    for row in rows:
        note = mapping.get(normalized(row["name"]))
        if not note:
            raise ValueError("식당 메모 대조 실패: " + row["name"])
        conn.execute(q("UPDATE lunch_review SET comment=? WHERE id=?"), (note,row["id"]))
    return len(rows)
