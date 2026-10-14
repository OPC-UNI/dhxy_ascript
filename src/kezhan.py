"""客栈任务自动化。

流程（与玄枢轶闻基本一致）：
0. 任务追踪中已有「客栈」任务（续跑）→ 直接跟踪（复用 xuanshu.follow_task）
1. 没有任务时，前往洛阳客栈领取任务：
   1.1 已在洛阳客栈 → 查找掌柜 zhanggui.png；未找到 → Alt+6 后重试一次，
       仍无 → 点击兜底点 (530,593) 后重试一次（用户实测有效），
       仍没有 → 终止任务
   1.2 不在洛阳客栈 → 世界地图 → 点击洛阳城 → 小地图点击 (884,600)
       - 出现「你要到洛阳客栈」对话框 → 点绿色选项「快送我去」
       - 未出现 → 等待寻路结束
   1.3 寻路结束后回到 1.1 逻辑（最多 NAV_MAX_ATTEMPTS 轮，防止死循环）
2. 点击掌柜对话 → 逐屏处理对话链直到任务进入追踪面板：
   「接取客栈任务」绿字 → 瑶池迎仙大会对话（点「广寒宫我熟悉的很，
   包在我身上！」）；跟踪期间出现该对话同样处理。
   每日次数已满时出现玄枢轶闻同款文案，结束任务循环
3. 跟踪任务直到完成 → 继续领取下一轮；达到次数上限时结束

跟踪与玄枢轶闻一致：点击任务追踪绿字下划线文案推进，战斗等待战斗线程处理
（follow_task 战斗中自动跳过识别）。特殊任务步骤「前往洛阳城大理寺墙外附近」
已实现手动寻路（世界地图 → 洛阳城 → 小地图 (620,317) → 到达 220,195 附近
→ 检查阿旺 awang.png）；神秘宝箱（追踪文案「前面出现一个神秘宝箱，快去」
出现时双模板匹配宝箱点击，到达遮挡时点屏幕中间让人物离开后再匹配）；
提交物品/无跟踪两类特殊任务后续单独处理。
"""
import time

import cv2
import numpy as np
from ascript.windows.hardware.keyboard import Keyboard
from ascript.windows.system import R

from .app_utils import log, human_click, move_away, norm_text
from . import app_utils  # 高性能模式共享设施（固定位置缓存点击）
from . import bag  # 物品栏开关
from . import map as game_map  # 地图操作（世界地图/小地图/位置识别）
from . import task_tracker  # 任务追踪面板操作
from .battle import is_in_battle, is_in_battle_frame  # 战斗状态判定（统一在 battle.py 中实现）
from . import debug_overlay  # 调试信息悬浮层（任务进度更新）
from .control_window import load_task_counts  # 任务次数上限（通用设置）
from .xuanshu import follow_task  # 共享跟踪引擎（kezhan 对 xuanshu 的唯一依赖）
from .dialog_common import (_click_green_option_once, _dialog_text_present,
                            _norm_dialog_present, close_scene_panels,
                            _click_screen_center, _fuzzy_contains,
                            DAILY_LIMIT_TEXT, DAILY_LIMIT_INFO,
                            DIALOG_LOSE_TEXTS, green_option_center)

# 任务追踪面板中的任务标题
TASK_MODE = "客栈"       # 控制面板任务模式名（任务次数配置键）
TASK_TITLE = "客栈任务"  # 追踪面板标题匹配串（如 月宫采珍【客栈任务】）
# 注意：不能只匹配「客栈」——地图位置文案 洛阳客栈[18,12] 也含「客栈」，
# 会被 track_task 误作任务锚点（实测第 12 轮跟踪失败根因）
# 任务结束文案（用户 2026-10-12 确认）：出现即表示客栈任务已结束，
# 终止任务循环（与「少侠已经完成5次任务」的每日上限同待遇）
KEZHAN_END_TEXT = "我这里已经没有你可以做的事情了，明天再来找我。"


class DailyLimitError(Exception):
    """客栈任务已结束（无更多任务可做），终止任务循环。"""

# ---- 前往洛阳客栈找掌柜 ----
KEZHAN_MAP = "洛阳客栈"               # 位置识别地名（精确匹配）
LUOYANG_CITY = "洛阳城"               # 世界地图城市名
ZHANGGUI_ICON = "zhanggui.png"       # 掌柜模板（实测素材）
ZHANGGUI_FALLBACK_CLICK_POS = (530, 593)  # 找不到掌柜时的兜底点击点（用户实测：点击后重试可找到）
INN_MAP_CLICK_POS = (884, 600)       # 洛阳小地图上客栈位置的点击点（窗口相对坐标）
FAST_DIALOG_TEXT = "你要到洛阳客栈"    # 点击后可能出现的快送对话框文案
FAST_DIALOG_OPTION = "快送我去"       # 快送对话框绿色选项
ARRIVE_TIMEOUT = 120                  # 等待寻路完成的超时（秒）
NAV_MAX_ATTEMPTS = 3                 # 前往客栈的最多尝试轮数（防死循环）
ALT6_RETRY_WAIT = 1.5                # Alt+6 后等待画面刷新的时间（秒）
# 领取任务对话框中的绿色选项（点击掌柜后出现）
DIALOG_OPTION_LINGQU = "接取客栈任务"
YAOCHI_DIALOG_OPTION = "广寒宫我熟悉的很，包在我身上！"  # 瑶池对话选项（高性能快速链用）
DIALOG_TIMEOUT = 10                  # 等待对话框出现的超时（秒）
# 任务对话处理（对话框文案 → 点击的绿色选项，同 xuanshu.DIALOG_OPTION_MAP）：
# 对话框出现相应文案时，点击其绿色选项推进。领取对话链与跟踪期间均按此处理，
# 后续新增对话直接在此追加条目。
DIALOG_OPTION_MAP = {
    "瑶池近日要办迎仙大会，宴上那道琉璃映月羹": YAOCHI_DIALOG_OPTION,
    "少侠有什么事吗": "客栈任务",
    "有空我请你喝瑶池鲜酿": "那我就坐等瑶池鲜酿了",
    "被镇于修罗古城中的妖邪近日隐隐躁动": "修罗出世非同小可，我这便前去加固封印",
    "火云戈壁的浮屠塔近来邪气外泄": "护法镇魔是我的强项，我这便前往。",
    "就凭你": "收拾你",
    "此间异象比老夫预想之中复杂万倍，实难在短期内探明全貌": "日后若有需要找我便是",
    "城东王老汉的小孙子阿旺前日出门玩耍，至今未归。有人说曾在大理寺墙外": "孩童走失非同小可，我这就去大理寺附近仔细寻找。",
    "我不回去！回去又要听爹娘摔碗砸锅": "阿旺朝比武场的方向跑去了，快去追上他吧",
    "地府为震慑怨念深重、不愿往生的凶魂，特在四方设下四象镇魂印": "阴阳有序，此事义不容辞。",
    "少侠代我去看看吧":"我这就动身",
    "还请少侠帮忙":"看我的",
    "我想看着她健健康康": "放心吧，你母亲定会早日康复的",
    "少侠可否帮忙寻一寻": "我这就去",
    "我也想念爷爷，我现在就回家！": "快回去吧，爷爷看到你一定会很高兴的！",
    "少侠不妨去那边": "没问题",
    "当初我决心要修炼成精的那一日，我本以为是短暂的分别":"哎",
    "阴阳通道重归稳定，多谢少侠出手相助。":"小事一桩，不足挂齿，告辞。",
    "蟠桃园近日闹了个偷桃精":"我倒要见识见识那精怪的本事",
    "那我今日便跟你拼了":"看打",
    "不过今日我还有事，明日我再来品鉴新熟的桃子哟~" : "下次见到定要再好好揍他",
    "灵兽大冒险":"没问题",
    "到时还请少侠也一同前往灵兽村观赛，届时灵兽齐聚，必有一番精彩较量！":"一定到场！",
    "女儿村的阿晴姑娘前些日子在长安酒楼订了一批上好的食材，说是要给她远":"这等喜事，自然要尽快送到，包在我身上。",
    "那婉儿姑娘性情温婉，容貌也出众，也不知是谁这么有福气能娶她做婆娘…":"好",
    "婉儿姑娘预定的食材都在这里了，有糯米、红枣，还有上等的花雕……":"还请姑娘清点一下",
    "他说要采我最爱的月影花...在回来的路上":"姑娘节哀...待我寻到那伙歹人，定要为姑娘讨回公道！",
    "近日长安城来了个自称“赌圣”的狂徒，在城中设下赌局，扬言无人能连胜":"赌圣？这么大的口气，我倒要见识见识。",
    "第一局简单，骰子比大，点数高者胜！":"没问题。",
    "赌圣张大千使劲晃动":"随意摇晃几下掷骰子",
    "杜必赢使劲晃动":"随意摇晃几下掷骰子",
    "杜必舒使劲晃动":"随意摇晃几下掷骰子",
    "啧...运气倒是不错":"随便你，换哪儿你都得输。",
    "不对不对，上次我们比大，这次我们该比小，这次不能作数，我还得换个地":"你怎么那么多事儿……",
    "今日...今日算我栽了！（慌乱收拾赌具，在众人嘘声中仓皇离去":"下次再出老千我可就不客气了！",
    "花果山传来急报，水帘洞中有只猴精突发癫狂，见人就扑。那猴儿平日最是":"猴精突然发狂？其中必有蹊跷，我这便去水帘洞一探究竟。",
    "你！你也是它们派来的！（猴精龇牙咧嘴直扑而来）":"只能先制服它再说了。",
    "有位整天云游的逍遥散仙刚向我发了份求助，说他家那只宝贝坐骑被蓬莱的":"小事一桩，包在我身上。",
    "看来这就是那困住灵兔的封印了。":"消耗灵气，破除封印",
    "多谢少侠，嘿嘿，老夫还要赶去赴瑶池仙会，后会有期！":"您老慢走",
    "绣娘苏巧儿的母亲突发急症，城中大夫皆束手无策":"性命攸关，我这就去求见孙神医。",
    "这帖药需分三次煎服：首服吊命，次服通脉，三服固本。切记要用晨露煎煮":"神医妙手仁心，我代苏家谢过！",
    "可谁知目前那双手，绣出了长安城最美的牡丹，却绣不出自己的安康……":"将九转还魂汤交给巧儿姑娘",
    "嗯！等娘亲醒了，我一定要把新学的双面绣法绣给她看……":"巧儿姑娘手艺精湛，待夫人醒来见到你的绣品，定会欣慰不已。",
    "说来有趣，昨日蟠桃园土地公来此小酌，临走时竟将他的地灵勘舆盘落在这":"土地公竟如此大意，我这就给他送去。",
    "坏了坏了！莫不是昨日贪杯，把勘舆盘落在客栈了？":"老人家可是在寻此物？",
    "这我就不便告知少侠了……天机不可泄露啊！不过老夫还有个不情之请":"好，不就是跑个腿嘛，我都习惯了。",
    "少侠这么快就回来了，办事果然效率，还得是你呀！":"这是蟠桃园土地让我带给你的礼物，也不知道是啥东西。",
    "嘿嘿，我就知道他会送我东西，那个……我要拆礼物了":"行，我走，小气鬼！",
    "女儿国落胎泉近来夜夜传来婴孩啼哭，扰得附近居民夜不能寐。少侠不如前":"夜半哭声必有冤情，我这就去落胎泉看个究竟。",
    "下次...下次一定要平平安安地来做娘亲的孩子..":"哎",
    "洛阳城突发诡异瘾症，已有多人出现癫狂症状":"救人要紧，我这就前往隐林涧。",
    "少侠来正是时候！此次癔症乃瘴气入心所致，患者先是神智昏沉，继而狂性":"还请孙神医即刻配药，让城中百姓免受癔症之苦。",
    "商行提取货物，代为运送？":"举手之劳，包在我身上。",
    "我处现有三类物资待运：其一为节庆用品":"节庆用品",
    "少侠可是为我们送物资而来？":"物资都在这里了。",
    "这批物资来得太及时了！我国“百花节”正缺这些筹备庆典。真是多谢少侠":"小事一桩，不必言谢。",
    "女儿国太师近日每夜被厉鬼入梦侵扰，以致心神俱疲，政务都耽搁了":"好说，我这便动身。",
    "这法阵马上便成，你休要坏我大事！":"原来太师的噩梦是你这妖僧搞的鬼！看打！",
    "难怪当日他执意要在泉眼处布阵……此贼必与当年因触犯国法被逐的妖道有":"那妖僧日后可能再来生乱，太师还需小心为上。",
    "连老渔民晒的鱼干都要抢走三成。村民敢怒不敢言，少侠可愿":"梆梆帮，什么破名字",
    "我看你像是来找事儿的！不过一一我就是一帮派喽啰，只负责收保护费":"你们还真是分工明确",
    "对对对！梆梆帮收保护费啦！不给钱的就要挨两拳！嗯，你是来交保护费的":"我是来梆梆揍你两拳的！",
    "长寿村有个钱老四在办“有奖问答”，可村民们都说此人惯会耍赖":"还有这等无赖？我定要让他愿赌服输。",
    "瞧一瞧看一看了！有奖问答！只需要压上点银子，能连续答对三题便可十倍":"给你银子，我倒是要试试",
    "好嘞，少侠可有准备好？":"准备好了。",
    "请听第一题：孙悟空的“如意金箍棒”原是大禹治水时的什么工具？":"定海神针",
    "再听第二题：牛魔王的坐骑是什么？":"避水金睛兽",
    "再听第三题：金角大王和银角大王原本是谁的童子？":"太上老君",
    "嗯，再听第四题：乌鸡国国王被谁推入井中？":"青毛狮子精",
    "最后一题：沙僧的武器是什么？":"降妖宝杖",
    "这群刁民……好汉不吃眼前亏，我先溜了！":"下次再用这种小伎俩行骗定要好好收拾他一顿……",
    "指导姑娘们的演奏，劳烦少侠跑一趟。":"指导音律我在行，这便去那边瞧瞧。",
    "少侠，我们等你好久啦！赶紧帮我们打个样如何？":"好说，看我的。",
    "少侠慢走，改日记得到云来酒店喝酒！":"没问题，告辞。",
    "那本是个痴情女子，因心上人久出不归便寻了短见。如今化作冤魂归来":"好，我这便去长寿村会会那厉鬼。",
    "连你也嫌我面目可憎？好！好！你们这些薄情之人，都该千刀万剐！":"我不是那意思，别动手啊！",
    "唉，罢了，终究是我太过执着。愿他此生……得偿所愿，平安喜乐吧":"愿你来世，能被温柔以待…",
    "近日有个天竺武士在长安擂台摆擂，口出狂言说我大唐无人能敌":"这就去教训教训他。",
    "嗬！又来个不知深浅的！我这天竺秘传的瑜伽体术，岂是你们唐人的花拳绣":"啰里啰嗦的，看打！",
    "别、别瞎说，我没招你没惹你，你可千万别跟我动手啊，小心我讹你医药费":"瞅你这点本事，算了，赶紧从我眼前消失。",
    "女儿国一位独居女子近来夜夜难眠，总说房中有黑影游荡、耳畔私语不断，":"夜不安枕，定有缘由。我这就去查看。",
    "恐怖的鬼魂不要":"看来就是你在搞鬼，看打！",
    # 妆匣遗思·小云姑娘「姐姐--」的选项是省略号「……」：norm_text 去
    # 标点后为空，模糊/绿字块兜底都匹配不了，走 _click_green_dots_below
    # 点阵兜底（六点亮绿 2x2px 点阵，实测 y410 x440-465）。键用 NPC
    # 名+台词拼接（裸「姐姐」会误配所有含「姐姐」的对话，如
    # 「等等！这气息…是晴儿姐姐？！」）；值「……」仅作语义标注。
    "小云姑娘姐姐":"……",
    "洛阳城正举办幸运五十二”答题大会，少侠博闻强识，不如替咱们客栈带":"你还真会占便宜，成，我这就去凑个热闹。",
    "规则简单的很！少侠只需连闯三关一一智多星考你经史，多智星问你天象，":"好说！",
    "请听经史类第一题：神话中，用五彩石补天的是哪位上古女神？":"女娲",
    "再听经史类第二题：“千里送鹅毛”的下半句俗语是？":"礼轻情意重",
    "再听经史类第三题：“完璧归赵”这个典故中，主角是谁？":"蔺相如",
    "请听天象类第一题：传统二十四节气中，紧跟在“清明”之后的是？":"谷雨",
    "再听天象类第二题：“银河”在民间传说里，又常被称为什么？":"天河",
    "再听天象类第三题：古代用来计时的“日晷”，主要利用的是什么投影原理":"太阳",
    "请听民生类第一题：“五谷丰登”中的“五谷”，通常不包括以下哪一种？":"瓜",
    "再听民生类第二题：“瑞雪兆丰年”，主要是因为积雪对农田有什么好处？":"冻死害虫，保温保墒",
    "再听民生类第三题：“柴米油盐酱醋茶”，常被用来泛指什么？":"日常生活",
    "哎呀呀！少侠学识如海，奈何今日运道似沙……不过别灰心，下次少侠定能":"怎么感觉被骗了",
    "据说那玄石乃上古修罗遗落的神器碎片所化，沉寂于修罗古城已千年。玄枢":"既然如此，我这就前去一探。",
    "少侠说的在理，那我便回玄枢阁复命了，告辞。":"后会有期。",
    "近日兰若寺不太平，有位书生冤魂常在暗处出没，逢人便要考问诗文，答不":"我上知天文下知地理，倒是要去领教领教。",
    "哼，来得正好！本公子最见不得胸无点墨之辈。你若能通过我的三题之试，":"请出题！",
    "请听第一题：子曰：学而时习之，不亦说乎？有朋自远方来，不亦乐乎？人":"不亦君子乎",
    "再听第二题：下列哪项不是大唐科举的常设科目？":"八股文",
    "再听第三题：三人行，必有我师焉。择其善者而从之，":"其不善者而改之",
    "再听第四题：“诗仙”指的是哪位唐代诗人？":"李白",
    "最后一题：我国传统历法中，用来表示季节更替和农事活动的“二十四节气":"太阳在黄道上的运行位置",
    "唉！这执念困我太久，的确该放下了......多谢点拨，我们来世文坛再会。":"来世再会！",
    "嘿嘿，天机不可泄露，少侠只管去小二那里收取物资按时送到即可。":"还搞得神神秘秘的...也罢，我这就送去。",
    "您路上千万稳着些，这包东西娇贵，可经不起颠簸！":"放心吧，我稳得很！",
    "孙婆婆，这是客栈掌柜托我给您带的物资，您清点一下看看。":"将物资交给孙婆婆",
    "嗐，哪有什么玄机！不过是些洛阳特产的薏米与百合，老身用来为大家熬安":"原是如此，我看那掌柜就是故意拿我寻开心的……",
    "流州境内近来有精怪作祟，此怪飘忽诡谲，似有未了执念。少侠可愿前去查":"没问题，既有蹊跷，我自当去探个明白。",
    "嘿嘿，就是你……你就是那最后一人！把你的头颅……借我一用！":"怎么还突然动手了？",
    "不重要了……只愿来世……能以真面目与他相遇……":"唉，又是一个被情所困的精怪……",
    "傲来国的木匠传来急信，说他为女儿国赶制的一批机关家具正缺几种关键材":"没问题，我这就动身。",
    "少侠小心！这屏风装着二十七道连环锁，碰错一处就要重装半日……对了，":"正是。",
    "多谢少侠！这是我给女儿国制作百鸟朝凤梳妆台一一这梳妆台卯时镜面会映":"成，那你忙着，我先告辞",
    "少侠听说没？那花果山呐，近来宝光冲天！可邪门的是，去寻宝的人个个都":"这宝贝合该与我有缘，我去看看！",
    "这是……万般寻觅，终是一场空的意思？":"这位空空兄倒是名副其实，害得我白跑一趟！",
    "此乃客栈掌柜托付的密信，请老君过目。":"将密信交给太上老君",
    "嗯……天机玄奥，非尔所能与闻。少侠且回罢！":"这……好吧。"

}
# 需要点击屏幕中间跳过的对话框文案 → 点击次数（出现则连点屏幕中间推进
# 对话，同 xuanshu.DIALOG_SKIP_TEXTS）；不同文案要推进的屏数不同，次数
# 按文案单独设置（可选 2/3/4/5/6 次），默认先统一 4 次，实测后逐条校准；
# 遇到无选项的对话在此追加
DIALOG_SKIP_TEXTS = {
    "少侠来得正及时！这月华霜蕊灵气充盈，正是宴上所需": 4,
    "附近的妖邪已被我清理干净了。": 4,
    "少侠可是受客栈所托，前往女儿国送货的？":2,
    "找到了！这宝箱藏得如此隐秘……呃，里面怎么只有一张字条？":2,
    "想不到阁下竟是饱学之士，在下佩服。":2,
    "四象归位，怨气已平":1,
    "少侠来此有什么事吗？":1,
    "少侠可曾探得什么线索？":4,
    "十年寒窗苦，一朝付火海！我不甘心！便是做鬼我也要考上功名！":2,
    "要不要请在场各位一同验看你的骰盅？":2,
    "莫要过来": 4,
    "你怎么也跟过来": 4,
    "有个身份不明的孩童逢人便问花花的下落": 4,
    "九百九十九……还差一个……就差最后一个……":2,
    "你口口声声说差一个，究竟所为何事？":3,
    "有劳少侠了，正是老身所需之物。":2,
    "又来个送银子的？我纵横江湖十载，骰盅一响黄金万两！少侠是来给我添彩":2,
    "你又来找我干嘛": 4,
    "我本是溪边一颗石子，花花自幼便叼着我在草地上打滚。那段被它捧在爪间": 4,
    "少侠可是为苏家娘子而来？今晨我已诊过脉象，":2,
    "少侠终于来了！娘亲为了供我学绣艺，日夜赶工熬坏了眼睛":1,
    "巧儿姑娘莫要难过，如今有孙神医的药，定能转危为安。":1,
    "你这和尚在这里鬼鬼祟祟的做什么？":1,
    "看来那妖僧乃假冒之人，意在催动落胎泉怨气为祸。如今虽已败退":1,
    "你是来交保护费的还是来找事儿的？":2,
    "梆梆给你两拳帮收保护费啦！不给钱的就要挨...挨...下一句词儿是啥来着":2,
    "哎……少侠答错了！":5,
    "你是何人？":4,
    "少侠来的正好":1,
    "恭喜少侠通过考验！来来来一一金指针停何处，福气便落谁家！":2,
    "哎呦！正是此物！":6,
    "世间皆负我……人人都欺我……": 5,
    "要不要请在场各位一同验看你的骰盅？":1,
    "等等！这气息…是晴儿姐姐？！":4,
    "恭喜少侠通关智多星的考验！接下来请前往多智星处接受下一轮考验！":1,
    "娘亲...为什么不要我...":6,
    "灵兽村近日打算举办一场名为“灵兽大冒险”的竞技活动，到时记得去参加": 3,
    "你、你要干嘛？我可和刚刚那个说大话的家伙不认识！":2,
    "隐林涧那边我已经通知完毕": 4,
    "长寿村近来有厉鬼作祟，惊扰乡邻":1,
    "少侠可是来取女儿村物资的？都给您备好啦！":2,
    "搞定了。": 4,
    "少侠有何事": 4,
    "姑娘的夫君想必快回来了吧？": 4,
    "就是这里了，我看此地风水不差，这次我定能赢你！": 4,
    "来来来，这次咱们比谁的点数小，一局定胜负！": 4,
    "走开，都走开": 4,
    "少侠来得正好，这儿有份女儿村孙婆婆急要的物资，劳烦帮忙送一趟。":2,
    "你赢了": 4,
    "甚好甚好……待老夫一观……":3,
    "近日玄枢阁收到密报，有上古玄石重现天日，正在广招能人前往探寻。少侠":2,
    "哎一一哟！又一位才俊登台！本届大会由洛阳府衙、多宝阁、东山商行联合":2,
    # 「少侠……」空屏（空等归人送食材时婉儿姑娘的停顿屏，实测无选项，
    # 点 1 次屏幕中间即推进；漏推进由下一轮询自愈）。
    # 不能只配「少侠」：norm_text 去标点后「少侠……」只剩「少侠」，模糊
    # 匹配会命中所有含「少侠」的对话框（掌柜「少侠有什么事吗」等）。
    # 用 NPC 名行+台词的拼接键（_dialog_text_present 会对全行拼接做
    # 匹配）唯一锁定本屏；其他 NPC 的裸「少侠……」空屏同法追加。
    "婉儿姑娘少侠": 1,
    # 「少侠……」空屏（空等归人送食材时婉儿姑娘的停顿屏，实测无选项，
    # 点 1 次屏幕中间即推进；漏推进由下一轮询自愈）。
    # 不能只配「少侠」：norm_text 去标点后「少侠……」只剩「少侠」，模糊
    # 匹配会命中所有含「少侠」的对话框（掌柜「少侠有什么事吗」等）。
    # 用 NPC 名行+台词的拼接键（_dialog_text_present 会对全行拼接做
    # 匹配）唯一锁定本屏；其他 NPC 的裸「少侠……」空屏同法追加。
    "婉儿姑娘少侠": 1,
}
# ---- 奇遇对话框（多选项恒点第一个；选项文案随奇遇变化不映射固定文案） ----
QIYU_DIALOG_TEXTS = ["阴风骤起",   # 第一幕：妖僧现身（三选项 护太师/破阵/远退）
                     "耳旁有异响",  # 第二幕：硬抗咒光后（两选项 退开/刺向源头）
                     "伤势过重",   # 结局幕：远退后摔伤（单选项「我知道了」）
                     # 妆匣遗思奇遇（实测 2026-10-12）：晴儿魂魄被吸入怪异
                     # 法器（三选项 飞身击碎法器/环视寻找法器主人/抽身远离，
                     # 恒点第一个「飞身而起，直接击碎法器」）
                     "刺耳尖叫",
                     # 同一奇遇下一幕：纵身而起时四周传出厉喝「此物碰不得」
                     # （两选项 攻势不减执意击落/闻声收势凝神戒备，
                     # 恒点第一个「攻势不减，执意击落」）
                     "此物碰不得",
                     # 答题大会奇遇（实测 2026-10-12）：组织者见官差来查
                     # （三选项 上前举报/讨要封口费/默默离开，
                     # 恒点第一个「赶紧上前向官差举报」）
                     "奖池银箱",
                     "官差面前举报",
                     "调查需要时间",
                     "客栈奇遇"]
# ---- 答题致富问答题（多星智；选项为纵向排列的单字，OCR 会把整列
#      合并成一条竖框——实测「稻麦瓜」识别为一个高矩形）----
# 问题关键词 → 正确选项单字。选项顺序若被游戏打乱，按「字在合并
# 文本中的位置」定位，不受顺序影响。新题在此追加。
QUIZ_ANSWER_MAP = {
    # 民生类第一题：「五谷」通常不包括哪一种（五谷=稻黍稷麦菽，瓜不是谷物）。
    # options=完整选项字列表（合并框逐字定位 + 答案字 OCR 漏读时消元兜底），
    # answer=正确选项。新题在此追加。
    "五谷丰登": {"answer": "瓜", "options": "稻麦瓜"},
}
# ---- 物品给与（高等食材→婉儿姑娘 / 九转还魂汤→苏巧儿；给与流程参考五环提交装备） ----
GAODENGSHICAI_ICON = "kezhan_gaodengshicai.png"  # 高等食材物品（给与面板检索）
JIUZHUAN_ICON = "kezhan__jiuzhuanhuanhuntang.png"  # 九转还魂汤物品（给与面板检索）
TAOZHI_ICON = "kezhan_taozhi.png"  # 桃枝物品（给与面板检索）
YIHELIPIN_ICON = "kezhan_yihelipin.png"  # 一盒礼品物品（给与面板检索）
JIGUANCAILIAO_ICON = "kezhan_jiguancailiao.png"
MIXIN_ICON = "kezhan_mixin.png"
GIVE_TITLE_ICON = "give_title.png"  # 给与面板标志（横幅「给与」静态标题区）
# 旧 GIVE_ICON=give.png（32x213 整条横幅）含随物品类别变化的动态区，实测
# 苏巧儿面板 0.906 → 桃枝面板 0.665 低于 0.8 阈值，面板检查会误判未打开；
# 改用横幅静态标题区模板（各面板类型 1.000/0.999 唯一命中）
GIVE_BTN_ICON = "give_btn.png"  # 给与按钮（与五环共用模板，OCR 读作「给子」）
GIVE_PANEL_TIMEOUT = 10         # 等待给与面板出现的超时
# 给与面板底部 NPC 行区域（实测「NPC：苏巧儿」位于 (543,579)-(647,598)，
# 窗口 1378x831 校准，留余量）；识别给与对象决定检索哪件物品
GIVE_PANEL_NPC_REGION = (520, 565, 680, 615)
# NPC 行关键词 → (物品模板, 物品名)；新增给与任务在此追加即可
GIVE_NPC_ITEMS = {
    "苏巧儿": (JIUZHUAN_ICON, "九转还魂汤"),
    "婉儿姑娘": (GAODENGSHICAI_ICON, "高等食材"),
    "蟠桃园": (TAOZHI_ICON, "桃枝"),
    "掌柜": (YIHELIPIN_ICON, "一盒礼品"),
    "女儿国": (YIHELIPIN_ICON, "一盒礼品"),
    "木匠": (JIGUANCAILIAO_ICON, "机关材料"),
    "太上老君": (MIXIN_ICON, "密信"),
}
GIVE_DEFAULT_ITEM = (GAODENGSHICAI_ICON, "高等食材")  # NPC 行识别失败时的兜底物品
# ---- 钱老四给与（问答斗智任务：面板无物品检索，输入 1000 银两参加活动） ----
QIANLAOSI_NAME = "钱老四"
QIANLAOSI_AMOUNT = "1000"
QIANLAOSI_INPUT_POS = (665, 553)  # 银两输入框中心（15.png 实测 (586-745,540-567)）
_qianlaosi_given = False          # 已点给与按钮（防面板关闭前重复点击二次给与）
# 任务追踪多绿字点击顺序（四象镇魂：按 青龙→白虎→朱雀→玄武 依次加固封印）；
# 关键词未命中时自动退回点第一个绿字，不影响其他任务
GREEN_CLICK_ORDER = ["青龙", "白虎", "朱雀", "玄武"]
# ---- 拍毒虫小游戏（入口对话框 → 开始 → 拍虫） ----
BUG_START_TEXT = "请在游戏开始后拍打毒虫，不要误伤蜻蜓和七星虫哦"  # 入口对话框文案
BUG_START_ICON = "bug_start.png"       # 开始按钮模板（38.png 实测素材）
DUCHONG_ICONS = ["duchong1.png", "duchong2.png", "duchong3.png"]  # 三种毒虫模板
BUG_CHECK_INTERVAL = 0.14              # 毒虫检索间隔（秒，用户指定 140ms）
BUG_TIMEOUT = 60                       # 小游戏总超时（秒）
BUG_IDLE_END = 5.0                     # 连续无虫出现达到该时长视为游戏结束（秒）
CLAIM_CHAIN_TIMEOUT = 30             # 整条领取对话链（含接取与任务对话）的总超时（秒）
# ---- 特殊任务步骤：前往洛阳城大理寺墙外（追踪文案出现时手动寻路） ----
DALI_TRACK_TEXT = "前往洛阳城大理寺墙外附近"   # 任务追踪文案（出现即需手动寻路）
DALI_MAP_CLICK_POS = (620, 317)               # 洛阳小地图上大理寺墙外的点击点（用户实测）
DALI_TARGET_POS = (220, 195)                  # 到达目标（洛阳城坐标，±5 容差）
DALI_ARRIVE_TIMEOUT = 120                     # 寻路等待超时（秒）
DALI_REDO_INTERVAL = 15                       # 寻路完成后最短重做间隔（任务文案未更新时防重复点击）
AWANG_ICON = "awang.png"                      # 阿旺 NPC 模板（图片后续提供）
AWANG_CLICK_OFFSET = 40                       # OCR 识别到名字标签后，点击目标在其正上方
                                              # 约 40px（用户实测：点文字本身无效）
# NPC 名字 OCR 兜底查找的排除区（任务追踪面板；面板内文案如「找阿旺。」不可点）
TRACK_PANEL_REGION = (0, 200, 260, 600)
# 任务追踪面板顶部小区域（标题行；客栈任务恒为面板首个任务，拍虫时快速检查用）
TRACK_TITLE_REGION = (0, 200, 260, 330)
_awang_missing_logged = False                 # 模板缺失仅记录一次，避免刷日志
_order_mismatch_logged = False                # 四象文本词/绿色词簇数不一致仅记录一次
_dali_last_done = 0.0                         # 上次大理寺寻路完成时间（防重复触发）
# ---- 特殊任务步骤：阿旺朝比武场方向跑（追踪文案出现时寻路到比武场） ----
BIWU_TRACK_TEXT = "阿旺朝洛阳城比武场方向跑"     # 任务追踪文案（出现即需寻路）
BIWU_MAP_CLICK_POS = (655, 620)               # 洛阳小地图上比武场的点击点（用户实测）
BIWU_TARGET_POS = (244, 12)                   # 到达目标（洛阳城坐标，±5 容差）
BIWU_ARRIVE_TIMEOUT = 120                     # 寻路等待超时（秒）
BIWU_REDO_INTERVAL = 15                       # 寻路完成后最短重做间隔
_biwu_last_done = 0.0                         # 上次比武场寻路完成时间（防重复触发）
# ---- 特殊任务步骤：神秘宝箱（追踪文案出现即执行开启流程） ----
BAOXIANG_TRACK_TEXT = "前面出现一个神秘宝箱，快去"   # 任务追踪文案（出现即执行宝箱流程）
BAOXIANG_ICONS = ("kezhan_baoxiang1.png",    # 宝箱双模板（用户实测提供，两形态）
                  "kezhan_baoxiang2.png")
BAOXIANG_THRESHOLD = 0.69                    # 模板匹配阈值（用户指定）
BAOXIANG_APPROACH_TIMEOUT = 20               # 接近阶段轮询超时（秒；宝箱在人物前方，步行短）
BAOXIANG_STEP_OFF_TIMEOUT = 15               # 点屏幕中间后等待人物离开宝箱的轮询超时（秒）
BAOXIANG_CONFIRM_TIMEOUT = 6                 # 点击宝箱后确认追踪文案消失的超时（秒）
BAOXIANG_REDO_INTERVAL = 15                  # 一次流程结束后的最短重做间隔（秒，防重复触发）
_baoxiang_last_done = 0.0                    # 上次宝箱流程结束时间（防重复触发）


def _at_inn(win):
    """判断是否已在洛阳客栈（位置地名精确匹配）。"""
    pos = game_map.read_position_with_recovery(win)
    return pos is not None and pos[0].strip() == KEZHAN_MAP


def _find_zhanggui(win):
    """查找掌柜模板；未找到时按 Alt+6 后重试，仍无则点击兜底点
    (530,593) 后重试一次（用户实测有效）。返回 (x, y) 或 None。

    模板实测匹配 0.806，NPC 待机动画会小幅波动，阈值取 0.75 留余量
    （实测 0.7 以上无其他误匹配）。掌柜随站位渲染位置变化，必须识图
    定位，不缓存。
    """
    pt = game_map.find_template(win, ZHANGGUI_ICON, threshold=0.75)
    if pt is not None:
        return pt
    log("[客栈] 未找到掌柜，按 Alt+6 后重试", "WARN")
    Keyboard.key_hot("alt", "6")
    time.sleep(ALT6_RETRY_WAIT)  # 等待画面刷新
    pt = game_map.find_template(win, ZHANGGUI_ICON, threshold=0.75)
    if pt is not None:
        log("[客栈] Alt+6 后已找到掌柜", "SYS")
        return pt
    # 用户实测兜底：点击 (530,593) 后重试（点位在对话框区域之下，
    # 即使有对话框打开，点击也不会落在选项上）
    log(f"[客栈] Alt+6 后仍无，点击兜底点 {ZHANGGUI_FALLBACK_CLICK_POS} 后重试", "WARN")
    human_click(win, ZHANGGUI_FALLBACK_CLICK_POS[0], ZHANGGUI_FALLBACK_CLICK_POS[1])
    time.sleep(ALT6_RETRY_WAIT)  # 等待点击响应/画面刷新
    pt = game_map.find_template(win, ZHANGGUI_ICON, threshold=0.75)
    if pt is not None:
        log("[客栈] 点击兜底点后已找到掌柜", "SYS")
    return pt


def _click_fast_travel_option(win, items, img_np):
    """检测快送对话框并点击绿色选项「快送我去」；点击成功返回 True。"""
    if not _norm_dialog_present(items, FAST_DIALOG_TEXT):
        return False
    return _click_green_option_once(win, items, img_np, FAST_DIALOG_OPTION, prefix="客栈")


def _try_fast_travel(win, img):
    """在当前帧检测快送对话框并点击「快送我去」；点击成功返回 True。

    快送对话框为居中对话框，区域 OCR（全帧 930ms → 区域 ~100ms）；
    帧由调用方提供（清理前预查与等待循环两处共用）。
    """
    items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
    return _click_fast_travel_option(win, items, img_np)


def _go_to_inn(win):
    """前往洛阳客栈：世界地图 → 点击洛阳城 → 小地图点击 (884,600)。

    点击后两种走向：
    - 出现「你要到洛阳客栈」对话框 → 点击「快送我去」快送到达
    - 未出现 → 等待寻路结束（到达洛阳客栈；位置稳定即视为寻路结束）
    返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮寻路"

    close_scene_panels(win, "客栈")
    # 世界地图 → 点击洛阳城，打开洛阳小地图
    ok, info = game_map.open_mini_map_city(win, game_map.WORLD_MAP_LUOYANG_ICON,
                                           LUOYANG_CITY)
    if not ok:
        return False, info
    human_click(win, INN_MAP_CLICK_POS[0], INN_MAP_CLICK_POS[1])
    log("[客栈] 已点击洛阳小地图 (884,600)，前往洛阳客栈", "SYS")
    time.sleep(1.0)  # 等待寻路/对话框响应
    # 先查快送对话框再清理地图：清理的右键回退点可能落在对话框上将其点掉
    img = win.capture(force_activate=False)
    if img is not None and _try_fast_travel(win, img):
        log("[客栈] 已点击「快送我去」，等待传送", "SYS")
        time.sleep(2.0)  # 等待传送完成
        return True, "已点击快送选项，传送洛阳客栈"
    close_scene_panels(win, "客栈")

    # 等待：快送对话框 / 到达洛阳客栈 / 寻路结束（位置稳定）。
    # 每轮询截一帧：战斗跳过与快送对话框判定同帧复用（原每轮询 3 次截图）
    deadline = time.time() + ARRIVE_TIMEOUT
    last = None
    unchanged = 0
    polls = 0
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is not None and is_in_battle_frame(img):
            time.sleep(2.0)
            continue
        if img is not None and _try_fast_travel(win, img):
            log("[客栈] 已点击「快送我去」，等待传送", "SYS")
            time.sleep(2.0)  # 等待传送完成
            return True, "已点击快送选项，传送洛阳客栈"
        # 位置识别（寻路等待期间不做 Alt+C 恢复：Alt+C 会打断自动寻路，
        # 失败跳过本轮即可），同时判定是否已到客栈
        pos = game_map.read_map_position(win)
        if pos is not None and pos[0].strip() == KEZHAN_MAP:
            return True, "已到达洛阳客栈"
        if pos is not None and pos == last:
            unchanged += 1
        else:
            unchanged = 0
        last = pos
        if unchanged >= 5:
            # 寻路已结束但未到客栈（点击无响应或目标位置变化），交给外层重试
            return False, "寻路结束但未到达洛阳客栈"
        polls += 1
        if polls % 5 == 0:
            log(f"[客栈] 前往客栈中... 当前位置: {pos or '识别失败'}", "INFO")
        time.sleep(1.5)
    return False, f"前往洛阳客栈超时（{ARRIVE_TIMEOUT}s）"


def _ensure_zhanggui(win):
    """确保在洛阳客栈并找到掌柜（用户流程 1.1~1.3）。

    返回 (掌柜坐标, 描述)；未找到返回 (None, 描述)。
    """
    for attempt in range(1, NAV_MAX_ATTEMPTS + 1):
        if _at_inn(win):
            pt = _find_zhanggui(win)
            if pt is not None:
                return pt, f"已在洛阳客栈并找到掌柜（第 {attempt} 轮）"
            # 在客栈但找不到掌柜（Alt+6 与兜底点击均已重试）：按用户要求终止任务
            return None, "在洛阳客栈但未找到掌柜（Alt+6 与兜底点击后仍无），终止任务"
        log(f"[客栈] 不在洛阳客栈（第 {attempt}/{NAV_MAX_ATTEMPTS} 轮），开始寻路", "SYS")
        ok, info = _go_to_inn(win)
        log(f"[客栈] {info}", "SYS" if ok else "WARN")
        if ok:
            # 快送/到达后回到 1.1 逻辑（下一轮循环检查）
            continue
        if "战斗" in info:
            return None, info
        time.sleep(1.0)  # 重试退避（导航失败快速重试，防紧循环）
    return None, f"多次尝试后仍未到达洛阳客栈（{NAV_MAX_ATTEMPTS} 次），终止任务"


def _has_task(win):
    """判断任务追踪中是否存在客栈任务（标题行匹配，避免内容行误判）。

    匹配串取「客栈任务」（标题形如 月宫采珍【客栈任务】）：
    单「客栈」会命中地图位置文案 洛阳客栈[18,12]（实测第 12 轮误判根因）。
    改用 task_tracker.has_task：只 OCR 追踪面板区域（约 0.9s，全窗 OCR
    约 2.2s），标题恒在面板左区且非绿字，判定语义与 get_task 一致；
    领取对话链轮询与每轮任务判定均随之提速。
    """
    return task_tracker.has_task(win, TASK_TITLE)


def _near_luoyang(pos, target):
    """判断是否在洛阳城目标坐标附近（±5 容差；复用 game_map.is_at_position）。"""
    return game_map.is_at_position(pos, LUOYANG_CITY, target[0], target[1], 5)


def _find_awang(win):
    """查找阿旺模板；awang.png 未提供时仅记录一次（不刷日志）。"""
    global _awang_missing_logged
    try:
        return game_map.find_template(win, AWANG_ICON, threshold=0.75)
    except Exception as e:
        if not _awang_missing_logged:
            log(f"[客栈] 阿旺模板不可用（{e}），改用 OCR 名字兜底", "WARN")
            _awang_missing_logged = True
        return None


def _find_awang_by_ocr(win):
    """全屏 OCR 查找 NPC 名字「阿旺」，返回点击坐标或 None。

    只匹配短行（≤4 字）含「阿旺」，排除任务追踪面板区域（0,200-260,600），
    避免误点追踪文案（如「找阿旺。」）。点击目标在名字标签正上方约
    40px（用户实测：点文字本身无效）。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return None
    items, _ = game_map.ocr_image(img)
    _, pt, pr, pb = TRACK_PANEL_REGION
    for txt, rect, score in items:
        if score < 0.55 or len(txt) > 4 or "阿旺" not in txt:
            continue
        if rect[0] < pr and pt <= rect[1] <= pb:
            continue  # 任务追踪面板内的文案，跳过
        cx = (rect[0] + rect[2]) // 2
        cy = (rect[1] + rect[3]) // 2 - AWANG_CLICK_OFFSET
        log(f"[客栈] OCR 识别到 NPC 名字「{txt}」@({cx},{cy})", "SYS")
        return cx, cy
    return None


def _check_awang(win):
    """检查阿旺 NPC：先模板 awang.png，未命中时全屏 OCR 名字「阿旺」兜底；
    找到则点击推进任务。返回 (是否成功, 描述)。
    """
    pt = _find_awang(win)
    if pt is None:
        pt = _find_awang_by_ocr(win)
    if pt is None:
        return False, "未找到阿旺（模板与 OCR 名字均未命中）"
    human_click(win, pt[0], pt[1])
    log(f"[客栈] 已点击阿旺 @({pt[0]},{pt[1]})", "SYS")
    return True, "已点击阿旺"


def _walk_to_luoyang_pos(win, click_pos, target_pos, timeout, desc):
    """洛阳城寻路通用流程：已在目标附近则跳过；已在洛阳城直接开小地图，
    否则世界地图 → 洛阳城 → 小地图；点击 click_pos 后等待到达目标附近。
    返回 (是否成功, 描述)。

    等待期间只用普通位置读取：Alt+C 恢复会打断自动寻路
    （实测按 Alt+C 后角色停在原地不再移动），识别失败跳过本轮即可。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮寻路"
    pos = game_map.read_position_with_recovery(win)
    if _near_luoyang(pos, target_pos):
        return True, f"已在{desc}附近"
    close_scene_panels(win, "客栈")
    if pos is not None and pos[0].strip() == LUOYANG_CITY:
        # 已在洛阳城：无需打开世界地图，直接打开小地图
        ok, info = game_map.open_mini_map(win)
    else:
        ok, info = game_map.open_mini_map_city(win, game_map.WORLD_MAP_LUOYANG_ICON,
                                               LUOYANG_CITY)
    if not ok:
        return False, info
    human_click(win, click_pos[0], click_pos[1])
    log(f"[客栈] 已点击洛阳小地图 {click_pos}，前往{desc}", "SYS")
    time.sleep(1.0)  # 等待自动寻路启动
    close_scene_panels(win, "客栈")
    # 等待到达目标附近
    deadline = time.time() + timeout
    polls = 0
    last = None
    unchanged = 0
    while time.time() < deadline:
        if is_in_battle(win):
            time.sleep(2.0)
            continue
        pos = game_map.read_map_position(win)
        if _near_luoyang(pos, target_pos):
            log(f"[客栈] 已到达{desc}附近", "SYS")
            return True, f"已到达{desc}附近"
        # 位置长时间不变（寻路被打断/无响应）→ 提前退出交给外层重试，
        # 不等满超时（识别失败 None 不计入）
        if pos is not None and pos == last:
            unchanged += 1
        else:
            unchanged = 0
        last = pos
        if unchanged >= 10:
            return False, f"寻路到{desc}无响应（位置 {pos} 长时间不变）"
        polls += 1
        if polls % 5 == 0:
            log(f"[客栈] 前往{desc}中... 当前位置: {pos or '识别失败'}", "INFO")
        time.sleep(1.5)
    return False, f"寻路到{desc}超时（未到达洛阳城 {target_pos[0]},{target_pos[1]} 附近）"


def _walk_to_dali(win):
    """前往洛阳城大理寺墙外并检查阿旺。返回 (是否成功, 描述)。"""
    ok, info = _walk_to_luoyang_pos(win, DALI_MAP_CLICK_POS, DALI_TARGET_POS,
                                    DALI_ARRIVE_TIMEOUT, "大理寺墙外")
    if not ok:
        return False, info
    return _check_awang(win)


def _walk_to_biwu(win):
    """前往洛阳城比武场（阿旺朝比武场方向跑），到达后搜索阿旺并点击。
    返回 (是否成功, 描述)。
    """
    ok, info = _walk_to_luoyang_pos(win, BIWU_MAP_CLICK_POS, BIWU_TARGET_POS,
                                    BIWU_ARRIVE_TIMEOUT, "比武场")
    if not ok:
        return False, info
    return _check_awang(win)


def _task_needs_dali_walk(task):
    """判断任务追踪文案是否要求前往大理寺墙外（模糊匹配，跨行兼容）。"""
    return (task is not None
            and _norm_dialog_present(task["lines"], DALI_TRACK_TEXT))


def _task_needs_biwu_walk(task):
    """判断任务追踪文案是否出现「阿旺朝洛阳城比武场方向跑」。"""
    return (task is not None
            and _norm_dialog_present(task["lines"], BIWU_TRACK_TEXT))


def _task_needs_baoxiang(task):
    """判断任务追踪文案是否出现「前面出现一个神秘宝箱，快去」。"""
    return (task is not None
            and _norm_dialog_present(task["lines"], BAOXIANG_TRACK_TEXT))


def _order_green_state(items, img_np):
    """四象任务绿字状态：返回 (仍绿关键词列表, 词簇中心列表, 区域原点)。

    对含顺序关键词的 OCR 行取并集区域做绿字掩码（只保留绿像素）；
    绿字列连通簇按阅读顺序排列，仍绿关键词 = 完整顺序 GREEN_CLICK_ORDER
    的后缀 n 个（n = 绿色词簇数；封印按序完成、完成的关键词变红，
    仍绿的必为后缀），从而在文字不变、仅颜色变化时也能精确判定每个
    关键词的绿字状态。词级判定不受 OCR 行合并/拆分抖动影响（关键词
    从各行拼接规范化文本中查找）；文本只用于定位搜索区域，不参与
    词簇对齐（OCR 漏读个别字会使文本词数少于词簇数，错位点击）。
    无顺序关键词的任务返回空。

    只取任务追踪面板内的行：对话框文案（如判官解释四象顺序）也可能含
    关键词，会把对话框的绿字选项误当成封印关键词（实测点到了 (711,391)
    的对话框选项）。
    """
    pl, pt_, pr, pb = TRACK_PANEL_REGION
    kw_lines = [(txt, rect) for txt, rect, _ in items
                if any(kw in norm_text(txt) for kw in GREEN_CLICK_ORDER)
                and rect[0] < pr and pt_ <= rect[1] <= pb]
    if not kw_lines:
        return [], [], (0, 0)
    l = min(rect[0] for _, rect in kw_lines)
    t = min(rect[1] for _, rect in kw_lines)
    r = max(rect[2] for _, rect in kw_lines)
    b = max(rect[3] for _, rect in kw_lines)
    region = img_np[t:b, l:r].astype(int)
    mask = task_tracker.green_mask(region, 90)
    # 文本中存在的顺序关键词（各行拼接后规范化查找，抗跨行拆分）
    joined = norm_text("".join(txt for txt, _ in kw_lines))
    kw_seq = [kw for kw in GREEN_CLICK_ORDER if kw in joined]
    if not mask.any() or not kw_seq:
        return [], [], (l, t)
    # 绿字 2D 连通域（字符级，每字一块），过滤噪点与「完成变红后
    # 残留的绿色下划线」（下划线高约 1-5px，文字高约 14px，h>=6 区分）
    ncomp, _, stats, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8)
    chars = []
    for i in range(1, ncomp):
        x, y, w, h, area = stats[i]
        if w >= 8 and h >= 6 and area >= 20:
            chars.append([x, y, x + w, y + h])
    # 同行相邻字符合并为词（y 差<=6、字符间距<=6px；实测字宽约 13px、
    # 词内字距约 2px、词间距约 17px），词按阅读顺序（先 y 后 x）
    words = []
    for c in sorted(chars, key=lambda c: (c[1], c[0])):
        if words and abs(c[1] - words[-1][1]) <= 6 and c[0] - words[-1][2] <= 6:
            words[-1][2] = max(words[-1][2], c[2])
            words[-1][3] = max(words[-1][3], c[3])
        else:
            words.append(c[:])
    comps = [((w[0] + w[2]) // 2, (w[1] + w[3]) // 2) for w in words]
    # 仍绿关键词 = 完整顺序的后缀 n 个，n = 绿色词簇数（按序完成 →
    # 绿的必为后缀）。不能按文本关键词数量对齐：OCR 会漏读个别字
    # （实测「白」偶发丢失 → 文本 3 词 vs 词簇 4 个），文本对齐会把
    # 词簇与关键词错位——白虎位置被当青龙点击、进度键在
    # 「青龙,朱雀,玄武」与「青龙,白虎,朱雀,玄武」间来回翻转
    # （2026-10-09 实测蹦跳点击根因）。
    global _order_mismatch_logged
    if len(comps) != len(kw_seq) and not _order_mismatch_logged:
        _order_mismatch_logged = True
        log(f"[客栈] 四象文本词({len(kw_seq)})与绿色词簇({len(comps)})数不一致"
            f"（OCR 漏读），按词簇数对齐", "WARN")
    n = len(comps)
    if n == 0:
        return [], [], (l, t)
    if n > len(GREEN_CLICK_ORDER):
        # 词簇多于顺序关键词（区域内混入关键词之外的绿字）：退回
        # 文本对齐（旧逻辑），此类场景需实测重新标定
        n = min(n, len(kw_seq))
        green_kws = kw_seq[-n:]
        return green_kws, comps[-n:], (l, t)
    green_kws = GREEN_CLICK_ORDER[-n:]
    return green_kws, comps, (l, t)


def _order_progress_key(items, img_np):
    """四象任务进度键：仍为绿字的顺序关键词列表（如 白虎,朱雀,玄武）。

    封印完成关键词变红（文字不变），绿字集合缩小即任务推进，
    follow_task 据此点击下一个绿字。词级判定不受 OCR 抖动影响。
    非四象任务（如智多星答题：题目文案变化推进）回退取任务首行内容
    作键——默认文本签名经相似度阈值（0.8）过滤，两个字的题目变化
    会被吞掉（实测 2026-10-13「经史类→天象类」相似 0.93 未触发
    再点击）；键为精确比较，且经两轮询确认过滤单次 OCR 抖动。
    """
    green_kws, _, _ = _order_green_state(items, img_np)
    if green_kws:
        return ",".join(green_kws)
    task = task_tracker.find_task(task_tracker.group_tasks(items), TASK_TITLE)
    if task is None:
        return ""
    lines = task.get("lines") or []
    return norm_text(lines[0][0]) if lines else ""


def _pick_order_green(items, img_np):
    """精确选中最早的绿字顺序关键词并返回窗口坐标点击点。

    行级选择无法区分同一行内的多个绿字（如 白虎 已红而同行的 朱雀
    仍绿），绿字词簇按阅读顺序与完整顺序的后缀一一对应，取最早仍绿
    的关键词词簇中心点击。未命中返回 None（回退 track_task 行级选择）。
    """
    green_kws, centers, (l, t) = _order_green_state(items, img_np)
    if not green_kws or not centers:
        return None
    cx, cy = centers[0]
    return l + cx, t + cy


def _find_duchong(win, threshold=0.75):
    """一次截图同时检索三种毒虫模板，返回首个命中的 (x, y) 窗口坐标。

    返回模板匹配中心（与 FindImages 约定一致）。每轮只截图一次、
    三个模板连续匹配，接近 140ms 的检索节奏。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return None
    scene = np.asarray(img)[:, :, :3]
    for icon in DUCHONG_ICONS:
        tmpl = cv2.imread(R.img(icon))
        if tmpl is None:
            log(f"[客栈] 毒虫模板不可用: {icon}", "WARN")
            continue
        res = cv2.matchTemplate(scene, tmpl, cv2.TM_CCOEFF_NORMED)
        _, maxv, _, maxl = cv2.minMaxLoc(res)
        if maxv >= threshold:
            h, w = tmpl.shape[:2]
            return maxl[0] + w // 2, maxl[1] + h // 2
    return None


def _has_task_fast(win):
    """快速检查任务追踪中是否仍有客栈任务（仅 OCR 追踪面板顶部一小块，
    客栈任务恒为面板首个任务；避免全窗 OCR 拖慢拍虫节奏）。
    截图失败时返回 True（不判定结束）。"""
    img = win.capture(force_activate=False)
    if img is None:
        return True
    items, _ = game_map.ocr_region(img, TRACK_TITLE_REGION)
    return task_tracker.find_task(task_tracker.group_tasks(items),
                                  TASK_TITLE) is not None


def _play_duchong(win):
    """拍毒虫小游戏：点击开始按钮 → 循环检索三种毒虫并点击。

    开始后每 BUG_CHECK_INTERVAL（140ms）检索一次毒虫模板（三种类型，
    每次只会出现一只），命中即点击。结束条件（满足其一）：
    1. 任务追踪中客栈任务消失（任务完成，每 2 秒快速检查一次）
    2. 连续 BUG_IDLE_END 秒无虫出现
    3. BUG_TIMEOUT 总超时兜底
    返回 (是否成功, 描述)。
    """
    pt = game_map.find_template(win, BUG_START_ICON, threshold=0.75)
    if pt is not None:
        human_click(win, pt[0], pt[1])
        log("[客栈] 已点击毒虫小游戏开始", "SYS")
        time.sleep(0.5)  # 等待游戏开始动画
    deadline = time.time() + BUG_TIMEOUT
    clicked = 0
    last_hit = time.time()
    last_task_check = 0.0
    while time.time() < deadline:
        pt = _find_duchong(win)
        if pt is not None:
            human_click(win, pt[0], pt[1])
            clicked += 1
            last_hit = time.time()
            log(f"[客栈] 拍中第 {clicked} 只毒虫 @({pt[0]},{pt[1]})", "SYS")
        elif time.time() - last_hit > BUG_IDLE_END:
            return True, f"拍毒虫结束（共拍 {clicked} 只）"
        # 客栈任务从追踪中消失即任务完成，结束小游戏。仅在无虫间歇检查
        # （游戏仅约 20 秒，OCR 检查约 1.1s，拍虫正忙时不做检查）
        now = time.time()
        if now - last_hit > 2.0 and now - last_task_check > 4.0:
            last_task_check = now
            if not _has_task_fast(win):
                return True, f"拍毒虫结束（共拍 {clicked} 只，客栈任务已消失）"
        time.sleep(BUG_CHECK_INTERVAL)
    return True, f"拍毒虫超时结束（共拍 {clicked} 只）"


def _match_baoxiang(img):
    """神秘宝箱双模板比分数匹配。

    宝箱是世界对象，屏上位置随人物站位变化，无固定条带——全帧匹配；
    仅在宝箱流程内轮询（有界、每 0.5s 一帧），不进 follow 热路径。
    两张模板各自取全局最高分，均达标时取分高者（相似模板互匹配
    双模板比分数，防单模板阈值误判）。返回窗口相对坐标或 None。
    """
    frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    best_score, best_pt = 0.0, None
    for icon in BAOXIANG_ICONS:
        score, pt = game_map.best_match_np(frame, icon)
        if score > best_score:
            best_score, best_pt = score, pt
    return best_pt if best_score >= BAOXIANG_THRESHOLD else None


def _baoxiang_done(win):
    """宝箱步骤完成判定：追踪文案消失或整个客栈任务消失。

    仅 OCR 追踪面板区域（~100-170ms）；截图失败返回 False（未完成）。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return False
    items, _ = game_map.ocr_region(img, task_tracker.TRACK_REGION)
    task = task_tracker.find_task(task_tracker.group_tasks(items), TASK_TITLE)
    if task is None:
        return True  # 整个客栈任务已消失（宝箱为最后一步）
    return not _norm_dialog_present(task["lines"], BAOXIANG_TRACK_TEXT)


def _confirm_baoxiang_done(win):
    """点击宝箱后确认完成：追踪文案从面板消失。"""
    deadline = time.time() + BAOXIANG_CONFIRM_TIMEOUT
    while time.time() < deadline:
        if _baoxiang_done(win):
            return True
        time.sleep(1.0)
    return False


def _open_shenmi_baoxiang(win):
    """神秘宝箱流程（用户实测 2026-10-13）：

    点击任务追踪绿字寻路（follow 每轮已点，此处兜底续走）→ 轮询双
    模板匹配（接近途中宝箱已可见则直接点击）→ 超时视为已到达、人物
    遮挡宝箱——点屏幕中间让人物离开 → 再轮询匹配点击。点击后确认
    追踪文案消失即完成（任务从追踪中消失）。
    返回 (是否完成, 描述)。
    """
    # 兜底点击绿字确保寻路进行（follow 本轮已点过；重复点击重发同一
    # 寻路目标，人物已在途/已到达时无副作用）
    ok0, info0 = task_tracker.track_task(win, TASK_TITLE,
                                         green_order=GREEN_CLICK_ORDER,
                                         green_picker=_pick_order_green)
    if not ok0:
        log(f"[客栈] 宝箱流程：绿字点击未成功（{info0}），继续轮询匹配", "WARN")
    # 阶段一：接近途中直接匹配（宝箱在人物前方，步行通常短暂）
    deadline = time.time() + BAOXIANG_APPROACH_TIMEOUT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        if is_in_battle_frame(img):
            return False, "战斗开始，宝箱流程中断"
        pt = _match_baoxiang(img)
        if pt is not None:
            human_click(win, pt[0], pt[1])
            log(f"[客栈] 神秘宝箱已点击 @({pt[0]},{pt[1]})", "SYS")
            if _confirm_baoxiang_done(win):
                return True, "神秘宝箱已开启，追踪文案消失"
            return False, "宝箱已点击但追踪文案未消失，稍后重试"
        time.sleep(0.5)
    # 阶段二：到达后人物遮挡宝箱——点屏幕中间让人物离开，再匹配
    _click_screen_center(win, times=1, prefix="客栈")
    log("[客栈] 已点屏幕中间，等待人物离开宝箱", "SYS")
    deadline = time.time() + BAOXIANG_STEP_OFF_TIMEOUT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        if is_in_battle_frame(img):
            return False, "战斗开始，宝箱流程中断"
        pt = _match_baoxiang(img)
        if pt is not None:
            human_click(win, pt[0], pt[1])
            log(f"[客栈] 神秘宝箱已点击 @({pt[0]},{pt[1]})", "SYS")
            if _confirm_baoxiang_done(win):
                return True, "神秘宝箱已开启，追踪文案消失"
            return False, "宝箱已点击但追踪文案未消失，稍后重试"
        time.sleep(0.5)
    return False, "两个阶段均未匹配到神秘宝箱"


def _handle_follow_extras(win, items, img_np):
    """follow 期间的附加处理。

    拍毒虫小游戏入口优先（对话框出现则完整处理）；大理寺/比武场寻路
    （阻塞执行；完成后各自 REDO_INTERVAL 内不重复触发）；防抖期内与
    无关时照常处理任务对话选项/跳过（实测：防抖期内对话处理被短路
    导致「莫要过来」跳过从未触发）。
    返回是否已处理。
    """
    global _dali_last_done, _biwu_last_done, _baoxiang_last_done
    task = task_tracker.find_task(task_tracker.group_tasks(items), TASK_TITLE)
    if not is_in_battle(win):
        # 拍毒虫小游戏暂不处理（毒虫模板未匹配成功，先人工操作）：
        # 模板就绪后恢复此段即可自动开始并拍虫
        if False and _norm_dialog_present(items, BUG_START_TEXT):
            ok, info = _play_duchong(win)
            log(f"[客栈] {info}", "SYS" if ok else "WARN")
            return True
        if _task_needs_dali_walk(task) \
                and time.time() - _dali_last_done >= DALI_REDO_INTERVAL:
            # 寻路阻塞执行，本轮 items 已过期，不再做对话处理
            ok, info = _walk_to_dali(win)
            _dali_last_done = time.time()
            log(f"[客栈] 大理寺寻路: {info}", "SYS" if ok else "WARN")
            return True
        if _task_needs_biwu_walk(task) \
                and time.time() - _biwu_last_done >= BIWU_REDO_INTERVAL:
            ok, info = _walk_to_biwu(win)
            _biwu_last_done = time.time()
            log(f"[客栈] 比武场寻路: {info}", "SYS" if ok else "WARN")
            return True
        if _task_needs_baoxiang(task) \
                and time.time() - _baoxiang_last_done >= BAOXIANG_REDO_INTERVAL:
            # 宝箱流程阻塞执行，本轮 items 已过期，不再做对话处理
            ok, info = _open_shenmi_baoxiang(win)
            _baoxiang_last_done = time.time()
            log(f"[客栈] 神秘宝箱: {info}", "SYS" if ok else "WARN")
            return True
        # 物品给与：给与面板打开时跨轮询续接（对话选项点击在
        # DIALOG_OPTION_MAP 中处理，点击后弹出给与面板在此完成）
        if _handle_give_panel(win):
            return True
        # 战斗失败/平局对话框：先关闭对话框，再重新点击任务追踪绿字
        # 推进（参考 xuanshu 同款处理）。follow 的共享处理在内层等待
        # 循环里，而战斗失败后任务签名不变，外层循环恒走「签名未变」
        # 分支只调本回调——共享处理到不了。对话框可能遮挡追踪面板
        # 区域，直接点绿字会点到对话框上（实测）——须先屏幕中间点击
        # 关闭，再让 track_task 重新截帧点击（旧帧已失效）
        if any(_norm_dialog_present(items, t) for t in DIALOG_LOSE_TEXTS):
            matched = [t for t in DIALOG_LOSE_TEXTS if _norm_dialog_present(items, t)]
            log(f"[客栈] 检测到「你输了/平局」对话（命中 {matched}），先关闭对话框", "SYS")
            _click_screen_center(win, times=1, prefix="客栈")
            time.sleep(0.3)  # 等待对话框关闭
            # 诊断：转储关闭后的对话框区域文案与追踪面板标题（复现排查用）
            try:
                img2 = win.capture(force_activate=False)
                if img2 is not None:
                    dlg, _ = game_map.ocr_regions(img2, (task_tracker.DIALOG_REGION,))
                    dlg_txt = " | ".join(t for t, _, s in dlg if s >= 0.55)
                    if len(dlg_txt) > 80:
                        dlg_txt = dlg_txt[:80] + "…"
                    titles = [t["title"] for t in task_tracker.read_tasks(win)]
                    log(f"[客栈] 关闭后状态: 对话框={dlg_txt or '(无)'}；面板标题="
                        f"{' / '.join(titles) if titles else '(无标题行)'}", "SYS")
            except Exception:
                pass
            ok3, info3 = task_tracker.track_task(win, TASK_TITLE,
                                                 green_order=GREEN_CLICK_ORDER,
                                                 green_picker=_pick_order_green)
            if ok3:
                log("[客栈] 已重新点击任务绿字推进", "SYS")
                return True
            log(f"[客栈] 「你输了/平局」对话中任务绿字点击失败: {info3}", "WARN")
    return _handle_dialog_options(win, items, img_np)


def _give_item_for_npc(win):
    """OCR 给与面板 NPC 行，返回 (物品元组, 规范化 NPC 文案)。

    按 GIVE_NPC_ITEMS 关键词匹配（实测面板底部文案「NPC：苏巧儿」）。
    识别失败时返回默认物品并输出 OCR 原文便于校准。NPC 文案供
    钱老四等特殊给与分支判断。
    """
    img = win.capture(force_activate=False)
    npc_txt = ""
    if img is not None:
        items, _ = game_map.ocr_region(img, GIVE_PANEL_NPC_REGION)
        for txt, _, score in items:
            if score < 0.55:
                continue
            ntxt = norm_text(txt)
            npc_txt += ntxt
            for key, item in GIVE_NPC_ITEMS.items():
                if key in ntxt:
                    return item, npc_txt
        if QIANLAOSI_NAME in npc_txt:
            return GIVE_DEFAULT_ITEM, npc_txt  # 钱老四：特殊给与由调用方分支处理，不告警
        txts = [t for t, _, s in items if s >= 0.55]
        log(f"[客栈] 给与面板 NPC 行未识别出对象（OCR: {txts or '(无)'}），"
            f"按默认物品处理", "WARN")
    return GIVE_DEFAULT_ITEM, npc_txt


def _give_money_qianlaosi(win):
    """钱老四给与（问答斗智任务）：点银两输入框 → 全选清空 → 输入
    1000 → 点给与按钮。返回是否已处理（面板仍在时同样返回 True，
    由调用方的已给与标记防重复点击）。"""
    human_click(win, QIANLAOSI_INPUT_POS[0], QIANLAOSI_INPUT_POS[1])
    time.sleep(0.3)
    Keyboard.key_hot("ctrl", "a")  # 全选清空默认值
    Keyboard.input(QIANLAOSI_AMOUNT)
    time.sleep(0.3)
    pt = game_map.find_template(win, GIVE_BTN_ICON)
    if pt is None:
        log("[客栈] 钱老四给与：未找到给与按钮，等待下次轮询重试", "WARN")
        return True
    human_click(win, pt[0], pt[1])
    log(f"[客栈] 钱老四给与：已输入 {QIANLAOSI_AMOUNT} 银两并点击给与按钮", "SYS")
    return True


def _handle_give_panel(win):
    """给与面板已打开时完成物品给与（参考五环 _do_give）。

    按面板 NPC 行识别给与对象（巧儿→九转还魂汤，婉儿→高等食材；
    钱老四→输入 1000 银两特殊给与）→ 检索物品（含储物格切换）
    → 点击选中 → 点击给与按钮完成。
    物品未找到/按钮未出现时返回 True（面板仍在，下次轮询续接重试）。
    返回是否已处理。
    """
    global _qianlaosi_given
    if game_map.find_template(win, GIVE_TITLE_ICON) is None:
        _qianlaosi_given = False  # 面板已关闭：复位防重复给与标记
        return False
    (icon, label), npc_txt = _give_item_for_npc(win)
    if QIANLAOSI_NAME in npc_txt:
        if _qianlaosi_given:
            return True  # 已点过给与按钮：等面板关闭，不重复点击
        if _give_money_qianlaosi(win):
            _qianlaosi_given = True
        return True
    item, desc = bag.find_item_with_slots(win, icon, label)
    if item is None:
        # 对象识别有误兜底：直接检测其余物品（不再切换储物格）
        for alt_icon, alt_label in GIVE_NPC_ITEMS.values():
            if alt_icon == icon:
                continue
            alts = bag.find_item_in_bag(win, alt_icon)
            if alts:
                item, desc, label = alts[0], "对象识别兜底", alt_label
                break
    if item is None:
        log(f"[客栈] {desc}", "WARN")
        return True
    log(f"[客栈] 找到{label}（{desc}，匹配度 {item.confidence:.2f}），点击选中", "SYS")
    human_click(win, item.x, item.y)
    time.sleep(0.5)  # 等待选中
    pt = game_map.find_template(win, GIVE_BTN_ICON)
    if pt is None:
        log("[客栈] 未找到给与按钮，等待下次轮询重试", "WARN")
        return True
    human_click(win, pt[0], pt[1])
    log(f"[客栈] 已点击给与按钮，{label}给与完成", "SYS")
    return True


def _click_green_option_fuzzy(win, items, img_np, text):
    """点击指定文案的绿色选项（模糊匹配，抗 OCR 个别字误读）；成功返回 True。

    优先精确子串；否则相似度 ≥0.8 兜底（实测区域裁剪 OCR 会把
    「阴阳有序，此事义不容辞。」读成「…此事文不容辞，」，精确匹配漏掉）。
    """
    nopt = norm_text(text)
    if not nopt:
        return False  # 纯标点选项（如省略号「……」）：norm 后为空，交给点阵兜底
    for txt, rect, score in items:
        if score < 0.55 or rect[0] < task_tracker.PANEL_MAX_X:
            continue  # 限定对话框区域，避免误点追踪面板的绿字链接
        ntxt = norm_text(txt)
        if nopt in ntxt or _fuzzy_contains(ntxt, nopt, threshold=0.8):
            if task_tracker.is_green_text(img_np, rect):
                # 合并行按比例取选项自身中心（见 dialog_common.green_option_center）
                cx, cy = green_option_center(img_np, rect, len(nopt), len(ntxt))
                human_click(win, cx, cy)
                # 高性能：记录选项固定位置（按标准文案做键，漂移自愈）
                app_utils.fixed_pos_set(f"option_{text}", win, cx, cy)
                log(f"[客栈] 已点击对话框绿色选项「{txt}」@({cx},{cy})", "SYS")
                move_away(win)  # 鼠标停留选项上会使其悬停变色，干扰后续绿字判定
                return True
    return False


def _find_qrect(items, qtxt):
    """定位问题行矩形：先整键逐行模糊匹配；拼接键（NPC 名+台词，
    如「小云姑娘姐姐」）逐行匹配不到时，用键的后缀逐行匹配
    （台词行是键的尾部），取最靠下（y 最大）的命中行。
    只认对话框区域行（x≥PANEL_MAX_X）：任务追踪面板可能含与对话框
    相同的文案（如步骤「四象归位，怨气已平，去告知判官」），
    面板行命中不应触发选项点击。找不到返回 None。
    """
    dlg = [(t, r, s) for t, r, s in items
           if r[0] >= task_tracker.PANEL_MAX_X]
    nqtxt = norm_text(qtxt)
    for txt, rect, _ in dlg:
        if _fuzzy_contains(norm_text(txt), nqtxt, threshold=0.85):
            return rect
    best = None
    for i in range(1, len(nqtxt) - 1):
        suf = nqtxt[i:]
        for txt, rect, _ in dlg:
            ntxt = norm_text(txt)
            if len(ntxt) >= 2 and _fuzzy_contains(ntxt, suf, threshold=0.9) \
                    and (best is None or rect[1] > best[1]):
                best = rect
    return best


def _click_green_option_fallback(win, items, img_np, qtxt):
    """选项 OCR 不可识别（如单字「哎」）时兜底：定位问题行，点击其下方
    80px 内的第一个亮绿文字块（选项链接为亮绿，世界暗绿噪点被过滤）。
    返回是否已点击。
    """
    qrect = _find_qrect(items, qtxt)
    if qrect is None:
        return False
    x0 = qrect[0] - 20
    y0 = qrect[3]
    # 右界封顶 800：对话框右侧边缘的绿色装饰条（实测 x 956-980，
    # 24x56 绿块）会被旧 x1=qrect[2]+300 纳入误点；选项实测最右 ~780
    x1 = min(qrect[2] + 300, 800)
    region = img_np[y0:y0 + 80, x0:x1].astype(int)
    if region.size == 0:
        return False
    mask = task_tracker.green_mask(region, 120)
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8),
                                                      connectivity=8)
    cands = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w >= 6 and h >= 6 and area >= 15:
            cands.append((y, x, w, h))
    if not cands:
        return False
    cands.sort()  # 阅读序，取第一个
    y, x, w, h = cands[0]
    cx = x0 + x + w // 2
    cy = y0 + y + h // 2
    human_click(win, cx, cy)
    log(f"[客栈] 选项「{qtxt}」OCR 不可识别，按问题行下方绿字块点击 @({cx},{cy})", "SYS")
    move_away(win)  # 鼠标停留选项上会使其悬停变色，干扰后续绿字判定
    return True


def _click_green_dots_below(win, items, img_np, qtxt):
    """省略号等纯标点选项兜底：OCR 不识别纯标点行（「……」norm 后为空），
    且圆点阵单点仅 ~2x2px（实测妆匣遗思小云姑娘「姐姐--」下方
    六点省略号，亮绿 (0,208,17)），绿字块兜底的 w≥6/h≥6/area≥15
    过滤会漏——按绿色像素行聚类，点击最大像素簇中心。
    返回是否已点击。
    """
    qrect = _find_qrect(items, qtxt)
    if qrect is None:
        return False
    x0 = qrect[0] - 20
    y0 = qrect[3]
    x1 = min(qrect[2] + 300, 800)  # 同绿字块兜底：排除右侧绿色装饰条
    region = img_np[y0:y0 + 80, x0:x1].astype(int)
    if region.size == 0:
        return False
    mask = task_tracker.green_mask(region, 90)
    ys, xs = np.where(mask)
    if len(xs) < 3:
        return False
    # 按行聚类（y 差 ≤3 合并；点阵 2px 高、正文块 14px 高均聚合为簇）
    clusters = []
    for py, px in sorted(zip(ys.tolist(), xs.tolist())):
        for c in clusters:
            if c["ymin"] - 3 <= py <= c["ymax"] + 3:
                c["pts"].append((py, px))
                c["ymin"] = min(c["ymin"], py)
                c["ymax"] = max(c["ymax"], py)
                break
        else:
            clusters.append({"pts": [(py, px)], "ymin": py, "ymax": py})
    if not clusters:
        return False
    best = max(clusters, key=lambda c: len(c["pts"]))
    xs2 = [p[1] for p in best["pts"]]
    ys2 = [p[0] for p in best["pts"]]
    cx = x0 + (min(xs2) + max(xs2)) // 2
    cy = y0 + (min(ys2) + max(ys2)) // 2
    human_click(win, cx, cy)
    log(f"[客栈] 选项「{qtxt}」为绿色点阵省略号（OCR 不可识别），"
        f"点击点阵中心 @({cx},{cy})", "SYS")
    move_away(win)  # 鼠标停留选项上会使其悬停变色，干扰后续绿字判定
    return True


def _handle_quiz_options(win, items, img_np):
    """答题致富单字选项问答：选项为纵向排列的单字（稻/麦/瓜），OCR
    会把整列合并成一条竖框（实测「稻麦瓜」一个高矩形）。按问题
    关键词查 QUIZ_ANSWER_MAP 的正确选项字：合并框时从框内文字定位
    该字序号、按框高等分点击该段中心（游戏打乱选项顺序也不受影响）；
    逐行识别（未合并）时直接点命中行。返回是否已处理。
    """
    for qkw, qinfo in QUIZ_ANSWER_MAP.items():
        ans = qinfo["answer"]
        opts = list(qinfo["options"])
        qrect = None
        for txt, rect, _ in items:
            if rect[0] >= task_tracker.PANEL_MAX_X \
                    and _fuzzy_contains(norm_text(txt), norm_text(qkw), threshold=0.85):
                qrect = rect  # 只认对话框区域（追踪面板同文案不触发）
                break
        if qrect is None:
            continue
        nans = norm_text(ans)
        merged = None
        single = None
        for txt, rect, score in items:
            if score < 0.55 or rect[0] < task_tracker.PANEL_MAX_X:
                continue
            if rect[1] < qrect[3] - 4 or rect[1] - qrect[3] > 200:
                continue  # 问题行下方 200px 内
            h = rect[3] - rect[1]
            w = rect[2] - rect[0]
            ntxt = norm_text(txt)
            if h >= 30 and w <= 60 and len(ntxt) >= 2:
                merged = (ntxt, rect)  # 合并竖框（多字堆叠）
                break
            if len(ntxt) == 1 and ntxt == nans:
                single = rect
        if merged is not None:
            ntxt, rect = merged
            # 段数取完整选项数（OCR 文字长度不可靠：答案字漏读时文字
            # 变短但框仍覆盖全部选项行，实测 2026-10-13「稻麦」两字框）
            n = len(opts)
            # 逐选项字定位：答案字可能被 OCR 误读/漏读（实测「瓜」漏读
            # 致 find 失败、外层兜底点中第一个选项「稻」）——其余选项字
            # 全部命中时，未命中的那一段即答案（消元定位，与顺序无关）
            idxs = [ntxt.find(ch) for ch in opts]
            ans_i = opts.index(ans)
            if idxs[ans_i] >= 0:
                pos = idxs[ans_i]
            else:
                missing = [i for i, p in enumerate(idxs) if p < 0]
                if len(missing) == 1:
                    pos = missing[0]
                else:
                    continue  # 无法定位（多个选项字未命中），交给外层兜底
            cx = (rect[0] + rect[2]) // 2
            cy = rect[1] + int((rect[3] - rect[1]) * (pos + 0.5) / n)
            human_click(win, cx, cy)
            log(f"[客栈] 答题选项合并竖框「{ntxt}」，答案「{ans}」为第"
                f" {pos + 1}/{n} 项 @({cx},{cy})", "SYS")
            move_away(win)
            return True
        if single is not None:
            cx = (single[0] + single[2]) // 2
            cy = (single[1] + single[3]) // 2
            human_click(win, cx, cy)
            log(f"[客栈] 答题选项单行命中答案「{ans}」@({cx},{cy})", "SYS")
            move_away(win)
            return True
    return False


def _handle_qiyu_dialog(win, items, img_np):
    """奇遇对话框：多个选项时恒点第一个（选项文案随奇遇变化，不能
    映射固定文案——实测三选项「以身作障，护太师周全/舍人破阵，
    断祸乱之根/抽身远退，明哲保身」）。

    定位问题行后，点击其下方第一条选项行（阅读序）。注意：奇遇
    选项为浅色文字非绿色（实测 BGR ≈190,191,205，is_green_text
    会全部排除），点文本矩形中心；加 200px 距离上限防误点远处
    NPC 名。
    返回是否已处理。
    """
    for qtxt in QIYU_DIALOG_TEXTS:
        qrect = None
        for txt, rect, _ in items:
            if rect[0] >= task_tracker.PANEL_MAX_X \
                    and norm_text(qtxt) in norm_text(txt):
                qrect = rect  # 只认对话框区域（追踪面板同文案不触发）
                break
        if qrect is None:
            continue
        cands = []
        for txt, rect, score in items:
            if score < 0.55 or rect[0] < task_tracker.PANEL_MAX_X:
                continue  # 只认对话框区域
            if rect[1] <= qrect[3] or rect[1] - qrect[3] > 200:
                continue  # 问题行下方 200px 内
            if rect[0] - qrect[0] < 100:
                continue  # 故事/注释行左对齐排除（选项缩进实测 126-181px）
            cands.append((rect, txt))
        cands.sort(key=lambda c: (c[0][1], c[0][0]))  # 阅读序取第一个
        if not cands:
            return False
        rect, txt = cands[0]
        cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
        human_click(win, cx, cy)
        log(f"[客栈] 奇遇对话框：已点击第一个选项「{txt}」@({cx},{cy})", "SYS")
        move_away(win)
        return True
    return False


def _handle_dialog_options(win, items, img_np):
    """处理任务对话：奇遇对话框（多选项点第一个）优先，其次
    DIALOG_OPTION_MAP 绿字选项，再按 DIALOG_SKIP_TEXTS 连点屏幕
    中间跳过（同 xuanshu 的处理顺序）。

    领取对话链与跟踪期间（follow_task 的附加对话处理回调）均使用，
    返回是否已处理。
    """
    # 任务结束文案：出现即终止任务（对话框区域限定，追踪面板同文案
    # 不触发；抛异常向上传播，run_loop 捕获后结束任务循环）
    if _dialog_text_present(
            [(norm_text(t), r, s) for t, r, s in items
             if r[0] >= task_tracker.PANEL_MAX_X],
            norm_text(KEZHAN_END_TEXT)):
        raise DailyLimitError("客栈任务已结束（我这里已经没有你可以做的事情了，明天再来找我）")
    if _handle_qiyu_dialog(win, items, img_np):
        return True
    if _handle_quiz_options(win, items, img_np):
        return True
    # 选项/跳过匹配只认对话框区域行（x≥PANEL_MAX_X）：任务追踪面板
    # 可能含与对话框相同的文案（如步骤「四象归位，怨气已平，去告知
    # 判官」），面板行命中不应触发点击（用户 2026-10-12 确认）
    norm_items = [(norm_text(t), r, s) for t, r, s in items
                  if r[0] >= task_tracker.PANEL_MAX_X]
    for dtxt, opt in DIALOG_OPTION_MAP.items():
        if not _dialog_text_present(norm_items, norm_text(dtxt)):
            continue
        if _click_green_option_fuzzy(win, items, img_np, opt):
            log(f"[客栈] 检测到任务对话，已点击绿色选项「{opt}」", "SYS")
            return True
        # 选项 OCR 不可识别（如单字「哎」）：按问题行下方绿字块点击
        if _click_green_option_fallback(win, items, img_np, dtxt):
            log("[客栈] 检测到任务对话（选项 OCR 不可识别，绿字块兜底点击）", "SYS")
            return True
        # 纯标点选项（如省略号「……」）：OCR 不识别、点阵单点 ~2x2px
        # 绿字块过滤也漏——按绿色像素行聚类点击点阵中心
        if _click_green_dots_below(win, items, img_np, dtxt):
            log("[客栈] 检测到任务对话（省略号点阵选项，点阵兜底点击）", "SYS")
            return True
    for dtxt, clicks in DIALOG_SKIP_TEXTS.items():
        if _dialog_text_present(norm_items, norm_text(dtxt)):
            # 命中即按文案对应次数连点推进（多屏对话无需枚举后续文案）
            _click_screen_center(win, times=clicks, prefix="客栈")
            return True
    return False


def _claim_task(win, pt):
    """点击掌柜并领取客栈任务。返回 (是否成功, 描述)。

    pt 为模板匹配中心（FindImages 返回匹配中心，实测与 NPC 人物本体一致），
    直接点击即可。

    对话链（逐屏推进，直到任务进入追踪面板）：
    掌柜 → 「接取客栈任务」绿字 → （瑶池迎仙大会对话 → 点「广寒宫我熟悉的
    很，包在我身上！」）→ 任务出现在追踪面板。
    每日次数已满时出现玄枢轶闻同款文案，提前结束。
    """
    human_click(win, pt[0], pt[1])
    log(f"[客栈] 已点击掌柜 @({pt[0]},{pt[1]})", "SYS")
    # 高性能快速链：对话链固定（掌柜 → 「接取客栈任务」→ 瑶池对话
    # 「广寒宫我熟悉的很，包在我身上！」），选项坐标齐备时按固定延时
    # 连点（免 OCR 轮询，实测点击掌柜→点接取选项 6s+），任务进追踪即
    # 成功；未成功回退识别对话链（瑶池对话不出现时第二击无害）
    if app_utils.high_perf_on():
        pos_lingqu = app_utils.fixed_pos_get(f"option_{DIALOG_OPTION_LINGQU}", win)
        pos_yaochi = app_utils.fixed_pos_get(f"option_{YAOCHI_DIALOG_OPTION}", win)
        if pos_lingqu is not None and pos_yaochi is not None:
            time.sleep(app_utils.HIGH_PERF_DIALOG_WAIT)
            human_click(win, pos_lingqu[0], pos_lingqu[1])
            log(f"[客栈][高性能] 已按缓存坐标点击「{DIALOG_OPTION_LINGQU}」", "SYS")
            time.sleep(app_utils.HIGH_PERF_DIALOG_WAIT)
            human_click(win, pos_yaochi[0], pos_yaochi[1])
            log(f"[客栈][高性能] 已按缓存坐标点击「{YAOCHI_DIALOG_OPTION}」", "SYS")
            deadline = time.time() + 4
            while time.time() < deadline:
                if _has_task(win):
                    return True, "已领取客栈任务（高性能）"
                time.sleep(0.5)
            log("[客栈][高性能] 缓存连点后任务未进追踪，回退识别对话链", "WARN")
    # 每轮询：截图 1 次 + 限对话框区域 OCR（全帧 ~930ms → 区域 ~100-170ms，
    # 实测点击掌柜→点击「接取客栈任务」间隔 6s+，其中每轮 ~2.9s 全耗在
    # has_task 前置检查与全帧 OCR）；has_task（追踪区 OCR ~0.9s）限频到
    # 每 4 轮与点击后——对话框出现前任务不可能已进追踪
    deadline = time.time() + CLAIM_CHAIN_TIMEOUT
    polls = 0
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.3)
            continue
        items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        if _norm_dialog_present(items, DAILY_LIMIT_TEXT):
            return False, DAILY_LIMIT_INFO
        # 接取选项优先；未出现时处理任务对话（两屏顺序不定，逐屏推进）
        handled = _click_green_option_once(win, items, img_np, DIALOG_OPTION_LINGQU, prefix="客栈")
        if not handled:
            _handle_dialog_options(win, items, img_np)
        polls += 1
        if handled or polls % 4 == 0:
            # 点击后立即校验；未点击时限频校验（防空转）
            if _has_task(win):
                return True, "已领取客栈任务"
        time.sleep(0.3)
    if _has_task(win):
        return True, "已领取客栈任务"
    return False, f"点击掌柜后未领取到客栈任务（对话链超时 {CLAIM_CHAIN_TIMEOUT}s）"


def _follow_kezhan(win):
    """客栈 follow_task 统一参数调用（四象顺序绿字点击 + 附加对话处理）。"""
    ok, info = follow_task(win, TASK_TITLE, prefix="客栈",
                           extra_dialog_handler=_handle_follow_extras,
                           green_order=GREEN_CLICK_ORDER,
                           progress_key=_order_progress_key,
                           green_picker=_pick_order_green)
    return ("done", info) if ok else ("fail", info)


def run_cycle(win):
    """执行一轮客栈任务检查：有任务则跟踪到完成，无任务则领取。

    任务判定三重保险（实测：比武场步骤后任务未完成却误判无任务、
    带任务跑回客栈领取失败）：
    1. 无任务判定隔 3 秒复核一次（OCR 噪声/面板刷新会瞬时漏检）
    2. 特殊寻路失败不中断本轮（交给 follow 与兜底处理器重试）
    3. 到达客栈后领前复核：任务其实还在则直接跟踪，不点掌柜

    Args:
        win: 游戏主窗口 Window 对象

    返回 (状态, 描述)；状态：done 完成一次任务 / daily_limit 每日次数已满 / fail 本轮失败
    """
    if is_in_battle(win):
        return "fail", "战斗中，跳过本轮（等待战斗结束）"

    # 无任务判定先复核一次（2s：任务判定已改区域 OCR，仍保留复核防瞬时漏检；
    # 若出现「有任务却误判没有」需调回 3s）
    if not _has_task(win):
        time.sleep(2.0)

    if _has_task(win):
        # 特殊任务步骤：追踪要求前往大理寺墙外/比武场 → 先手动寻路
        # （绿字点击的自动寻路不适用；跟踪期间 _handle_follow_extras 同样兜底）
        global _dali_last_done, _biwu_last_done
        task = task_tracker.get_task(win, TASK_TITLE)
        if _task_needs_dali_walk(task) \
                and time.time() - _dali_last_done >= DALI_REDO_INTERVAL:
            ok, info = _walk_to_dali(win)
            _dali_last_done = time.time()
            log(f"[客栈] 大理寺寻路: {info}", "SYS" if ok else "WARN")
        if _task_needs_biwu_walk(task) \
                and time.time() - _biwu_last_done >= BIWU_REDO_INTERVAL:
            ok, info = _walk_to_biwu(win)
            _biwu_last_done = time.time()
            log(f"[客栈] 比武场寻路: {info}", "SYS" if ok else "WARN")
        return _follow_kezhan(win)

    # 没有任务 → 前往洛阳客栈找掌柜并领取任务
    pt, info = _ensure_zhanggui(win)
    if pt is None:
        return "fail", info
    # 到达客栈后领前复核：任务其实还在（此前误判）→ 直接跟踪，不点掌柜
    if _has_task(win):
        log("[客栈] 领前复核发现任务仍在追踪中，改为直接跟踪", "WARN")
        return _follow_kezhan(win)
    ok, info = _claim_task(win, pt)
    if not ok:
        return ("daily_limit", info) if info == DAILY_LIMIT_INFO else ("fail", info)

    # 领取成功：紧接着跟踪任务，不等轮次间隔
    return _follow_kezhan(win)


def run_loop(win, cycle_interval=1.0):
    """客栈主循环：跟踪完成的任务 → 回去继续领取 → 往复执行。

    结束条件（满足其一即返回结束原因，任务随之停止）：
    1. 完成任务次数达到设置的次数上限（通用设置-任务次数）
    2. 出现结束文案（「少侠已经完成5次任务」/「我这里已经没有你可以
       做的事情了，明天再来找我」）→ 每日次数已满/任务链结束
    3. 在洛阳客栈找不到掌柜（Alt+6 与兜底点击 (530,593) 后仍无）→ 终止任务
    返回结束原因描述。
    """
    max_rounds = load_task_counts().get(TASK_MODE, 0)
    log(f"[客栈] 任务次数上限: {max_rounds if max_rounds > 0 else '不限'}", "SYS")
    done = app_utils.task_progress_done(win, TASK_MODE)  # 当天已完成（跨重启持久）
    if done:
        log(f"[客栈] 恢复当天进度: 已完成 {done} 次", "SYS")
    if 0 < max_rounds <= done:
        log(f"[客栈] 当天进度已达上限 {done}/{max_rounds}，无需再执行", "SYS")
        return f"已完成 {done} 次客栈任务，达到次数上限 {max_rounds}"
    round_no = 0
    while True:
        round_no += 1
        log(f"[客栈] ==== 第 {round_no} 轮 ====", "SYS")
        try:
            status, info = run_cycle(win)
        except DailyLimitError as e:
            log(f"[客栈] {e}（已完成 {done} 次）", "SYS")
            return str(e)
        if status == "done":
            done += 1
            app_utils.task_progress_add(win, TASK_MODE, done)
            total = max_rounds if max_rounds > 0 else "∞"
            debug_overlay.set_progress(f"{done}/{total}")
            log(f"[客栈] {info}（{done}/{total}）", "SYS")
            if 0 < max_rounds <= done:
                return f"已完成 {done} 次客栈任务，达到次数上限 {max_rounds}"
        elif status == "daily_limit":
            log(f"[客栈] {info}（已完成 {done} 次）", "SYS")
            return f"每日任务次数已满（已完成 {done} 次）"
        else:
            log(f"[客栈] {info}", "WARN")
            # 找不到掌柜等终止性失败直接结束任务（由 run_cycle 返回的描述判定）
            if "终止任务" in info:
                return info
        time.sleep(cycle_interval)  # 轮次间隔，避免空转
