"""Human-authored questions and reference facts, frozen BEFORE model generation.

Gold answers/questions are never passed to generation or indexed as knowledge.
Sources: official LAMMPS, CODESYS, PostgreSQL 16, Python 3.12 and sklearn docs.
"""
import hashlib
import json
from pathlib import Path
from benchmark50_sources import OUT

# key | title | production scope | explicit question | paraphrase question | reference facts
ROWS = r'''
MAT-units|LAMMPS metal 单位制与切换约束|metal 单位及单位切换边界|LAMMPS units metal 的时间、能量、压力分别是什么单位？|金属原子模拟采用 metal 单位制，日志的时间、能量和压强该按什么读？|时间 ps；能量 eV；压力 bar。
MAT-boundary|LAMMPS 固定与收缩边界|p/f/s/m 边界行为和原子丢失条件|LAMMPS boundary 中 f 和 s 有什么区别？|模拟盒使用固定非周期边界时粒子越界会怎样，换成随原子范围收缩的边界有什么不同？|f 固定边界，越界原子在邻居重建时删除，通常导致 lost atoms 错误；s 根据原子位置收缩包围盒。
MAT-lattice|LAMMPS 晶格定义与原子创建|lattice 与 create_atoms 的职责及 scale|LAMMPS lattice 命令会创建原子吗？|已经给分子动力学程序定义晶格，为什么还没有原子，下一步需要哪条命令？|lattice 只定义晶格，不创建原子；需要 create_atoms 创建原子。
MAT-pair_style|LAMMPS 势函数与参数配置|pair_style 和 pair_coeff 的职责|LAMMPS pair_style 与 pair_coeff 分别做什么？|模拟中选了原子间相互作用形式以后，在哪里指定各原子类型之间的势参数？|pair_style 选择相互作用势形式；pair_coeff 设置原子类型对的系数/参数。
MAT-neighbor|LAMMPS 邻居列表 skin 权衡|skin 对邻居列表范围与重建成本的影响|LAMMPS neighbor 的 skin 增大会怎样？|原子模拟把势截断外的邻居缓冲距离调大，是每步计算更少还是列表重建更少？|skin 更大使列表包含更多原子，每步检查更多对；可减少邻居列表重建频率。不能保证总是更快。
MAT-timestep|LAMMPS 时间步与 rRESPA 外循环|timestep 单位、metal 默认值、rRESPA 含义|LAMMPS metal 的默认 timestep 是多少？|金属单位制下默认积分时间间隔是多少，使用 rRESPA 时 dt 指内循环还是最外层？|metal 默认 0.001 ps；rRESPA 的 dt 是最外层/最大时间步。
MAT-minimize|LAMMPS 能量最小化停止条件|minimize 四个参数及何时停止|LAMMPS minimize 的 etol、ftol、maxiter、maxeval 是什么？|做结构弛豫时，能量容差、力容差、迭代次数和力能量计算次数要全部满足才停止吗？|etol 能量相对容差，ftol 力容差，maxiter 最大迭代，maxeval 最大力/能量计算；任一停止条件满足即可停止。
MAT-fix_nh|LAMMPS NVT 与 NPT 系综控制|NVT/NPT 控温控压的差别及积分职责|LAMMPS fix nvt 和 fix npt 的区别？|材料模拟只保持温度与体积，和还要调节压强、盒尺寸，分别使用哪种积分 fix？|nvt 控温并保持体积；npt 控温控压，盒尺寸可变化；两者都执行时间积分。
MAT-fix_deform|LAMMPS 工程与真实应变率|fix deform erate 与 trate 定义|LAMMPS fix deform 的 erate 与 trate 有何区别？|拉伸模拟希望盒长随时间线性增长或指数增长，应分别选择哪种应变率方式？|erate 为恒定工程应变率、盒长线性变化；trate 为恒定真实应变率、盒长指数变化。
MAT-compute_stress_atom|LAMMPS 原子应力的符号与量纲|stress/atom 的量纲、符号及体积归一化|LAMMPS compute stress/atom 输出就是压力吗？|把每原子应力直接作为 Pa 读取对吗，它与压力张量有什么符号和体积关系？|输出是负的压力张量乘体积，单位为压力乘体积；得到应力需除适当体积，原子体积并非总能良好定义。
PLC-TON|CODESYS TON 接通延时|TON 输入边沿、Q 与 ET 条件|CODESYS TON 什么时候 Q=true？|PLC 输入刚变为真就要等一段时间才输出，等待中输入变假，计时与输出会怎样？|TON 上升沿启动；IN 为真且经过 PT 后 Q 真；下降沿重置计时，IN 假则 Q 假。
PLC-TOF|CODESYS TOF 断开延时|TOF 输入边沿、Q 与 ET 条件|CODESYS TOF 哪个边沿启动延时？|PLC 输入从真变假后仍保持输出一段时间，延时何时启动、何时关输出？|TOF 下降沿启动计时；IN 假且 PT 到期 Q 假；IN 真时 Q 真，上升沿重置计时。
PLC-TP|CODESYS TP 脉冲定时器|TP 触发条件与脉冲时长|CODESYS TP 如何触发脉冲？|PLC 要在布尔信号上升时输出指定长度脉冲，应使用哪个标准定时器、时长由谁指定？|TP；IN 上升沿启动；脉冲宽度由 PT 指定。
PLC-R_TRIG|CODESYS R_TRIG 上升沿检测|CLK 与 Q 的上升沿含义|CODESYS R_TRIG 的 Q 表示什么？|布尔输入从假变真时才报告事件，标准库中使用哪个触发块，接哪个输入？|R_TRIG 检测上升沿；输入 CLK；Q 为真表示检测到上升沿。
PLC-F_TRIG|CODESYS F_TRIG 下降沿检测|CLK 与 Q 的下降沿含义|CODESYS F_TRIG 的 Q 表示什么？|布尔输入从真变假时报告事件，标准库用哪个触发块，输入端叫什么？|F_TRIG 检测下降沿；输入 CLK；Q 为真表示检测到下降沿。
PLC-CTU|CODESYS CTU 加计数器|上升沿计数、RESET、PV 和 Q|CODESYS CTU 的 Q 何时为真？|PLC 累计脉冲数量时，什么事件使计数加一，复位后计数是多少，达到预置值时哪个输出置真？|CU 上升沿 CV 加一；RESET 真将 CV 置 0；CV>=PV 时 Q 真。
PLC-CTD|CODESYS CTD 减计数器|CD 上升沿、LOAD 和零值输出|CODESYS CTD 如何装载初始计数？|PLC 倒计数要从预设数开始，装载、每次减一与到零信号分别对应什么？|LOAD 真将 CV 设为 PV；CD 上升沿 CV 减一；CV=0 时 Q 真。
PLC-CTUD|CODESYS CTUD 双向计数器|双向计数、装载、复位、上下限输出及 WORD 类型|CODESYS CTUD 的 QU 与 QD 条件是什么？|PLC 双向计数器达到预置上限和减到零，分别哪个输出为真？该实现的 PV 是 WORD 还是 INT？|QU 在 CV>=PV 为真；QD 在 CV=0 为真；CODESYS PV 为 WORD，文档指出 IEC 定义为 INT。
PLC-RS|CODESYS RS 复位优先锁存器|RS 同时置位复位的优先级|CODESYS RS 的 SET 和 RESET1 都为真会怎样？|复位优先的双稳态块同时收到置位和复位，输出 Q1 应为真还是假？|RS 复位优先；SET 与 RESET1 同真时 Q1 假。
PLC-SR|CODESYS SR 置位优先锁存器|SR 同时置位复位的优先级|CODESYS SR 的 SET1 和 RESET 都为真会怎样？|置位优先的双稳态块同时收到置位和复位，输出 Q1 应为真还是假？|SR 置位优先；SET1 与 RESET 同真时 Q1 真。
DB-XACT-READ-COMMITTED|PostgreSQL 16 读已提交快照|Read Committed 默认级别与逐语句快照|PostgreSQL 16 Read Committed 的两次 SELECT 能看到不同数据吗？|同一数据库事务里前后两次普通查询，期间别人提交了更新，默认隔离级别是否可能读到不同结果？|默认 Read Committed；每条命令有新快照；两次 SELECT 可能读到不同的已提交数据。
DB-XACT-REPEATABLE-READ|PostgreSQL 16 可重复读与重试|Repeatable Read 快照与并发更新失败|PostgreSQL 16 Repeatable Read 遇到并发更新失败怎么办？|数据库可重复读事务因 concurrent update 无法序列化，是只重试最后一句 SQL 还是重跑整个事务？|应用须重试整个事务；可重复读使用首个非事务控制语句时的快照。
DB-XACT-SERIALIZABLE|PostgreSQL 16 可串行化与重试|Serializable 效果、监控和序列化失败|PostgreSQL 16 Serializable 是否保证永不失败？|最严格的数据库隔离级别等效串行执行，发生 serialization failure 时应用还要做什么？|可串行化模拟串行执行效果；仍可能序列化失败；应用须重试整个事务。
DB-DDL-CONSTRAINTS-CHECK-CONSTRAINTS|PostgreSQL 16 CHECK 与 NULL|CHECK 的真/空语义和非空约束边界|PostgreSQL 16 CHECK(price>0) 会拒绝 NULL 吗？|给价格列加大于零检查后，空价格仍然能插入，这是为什么，要另加什么？|CHECK 表达式为真或 NULL 都视为满足；拒绝 NULL 需 NOT NULL。
DB-DDL-CONSTRAINTS-NOT-NULL|PostgreSQL 16 非空约束|NOT NULL 与 CHECK 非空的关系|PostgreSQL 16 NOT NULL 和 CHECK(x IS NOT NULL) 比较？|数据库里禁止一列为空，专用非空约束与用检查表达式限制相比有什么效率区别？|NOT NULL 与 CHECK(col IS NOT NULL) 功能等价；显式 NOT NULL 更高效。
DB-DDL-CONSTRAINTS-UNIQUE-CONSTRAINTS|PostgreSQL 16 唯一约束与空值|UNIQUE 默认 NULL 语义和 NULLS NOT DISTINCT|PostgreSQL 16 UNIQUE 允许多个 NULL 吗？|唯一列里多个空值没有冲突，怎样让空值也被当作彼此相同？|默认 UNIQUE 认为 NULL 不相等，允许多个；NULLS NOT DISTINCT 将 NULL 视为相同。
DB-DDL-CONSTRAINTS-PRIMARY-KEYS|PostgreSQL 16 主键约束|主键唯一非空及数量限制|PostgreSQL 16 主键允许 NULL 吗？|给表指定主键自动获得哪些数据约束，一张表可以声明两个主键吗？|主键唯一且非空；每表最多一个主键；可以是多列组合。
DB-DDL-CONSTRAINTS-FK|PostgreSQL 16 外键与级联删除|外键引用完整性、CASCADE 与 RESTRICT|PostgreSQL 16 外键 ON DELETE CASCADE 做什么？|删除父表记录时想让引用它的子表记录一并删除，外键该选哪种删除动作，与 RESTRICT 有什么区别？|CASCADE 自动删除引用行；RESTRICT 阻止被引用行被删除。
DB-indexes-partial|PostgreSQL 16 部分索引匹配条件|部分索引谓词和参数化查询限制|PostgreSQL 16 部分索引何时能被查询使用？|只给部分行建索引后，查询条件要满足什么才能利用它，参数化条件是否总能匹配？|查询 WHERE 必须可推导出/蕴含索引谓词，规划时匹配；参数化条件不总能被证明，不能保证使用。
DB-indexes-multicolumn|PostgreSQL 16 多列 B-tree 索引|前导列等值、首个非等值列的扫描范围|PostgreSQL 16 多列 B-tree 哪些列条件限制扫描范围？|复合 B-tree 索引左侧连续等值条件之后第一列范围条件，与更右列条件，对扫描范围的作用相同吗？|前导等值列和首个无等值列上的不等式限制扫描部分；更右列检查能减少回表但不缩小必须扫描的索引部分。
PY-dump|Python 3.12 JSON 文本写入|dump 文本输出、ensure_ascii、allow_nan 及 framing|Python json.dump 写文件需要文本还是二进制流？|Python 把对象写成 JSON，输出是 str 还是 bytes，同一文件连续 dump 多个对象能直接成为一个合法 JSON 文档吗？|输出 str，流须接受文本；JSON 无 framing，连续 dump 多对象会产生非法 JSON 文件。
PY-load|Python 3.12 JSON 文件读取|load 支持的文件流、解析失败及编码|Python json.load 支持二进制文件吗？|Python 从文件读取 JSON，二进制流只允许哪些编码，内容不是合法 JSON 会抛什么异常？|支持二进制流，编码 UTF-8/UTF-16/UTF-32；非法 JSON 抛 JSONDecodeError。
PY-reader|Python 3.12 CSV 逐行读取|reader newline、行数据类型和自动类型转换边界|Python csv.reader 应怎样打开文件？|Python 逐行读 CSV 时 newline 应怎么设，每列会默认变成数字吗？|文件用 newline=''；默认每列是字符串，不自动转换；QUOTE_NONNUMERIC 例外。
PY-writer|Python 3.12 CSV 写入与空值|writer newline 和 None 的不可逆转换|Python csv.writer 如何写出 None？|把数据库空值写入 CSV 后还能靠原文件区分 None 与空字符串吗？|None 写成空字符串，此转换不可逆，无法仅凭此值区分。
PY-DictReader|Python 3.12 CSV 字典读取缺失列|DictReader 默认表头、restkey 与 restval|Python csv.DictReader 缺字段或多字段如何处理？|CSV 某一行比表头多列或少列，字典读取器分别把多出的内容放哪里、缺的补什么？|多字段放 restkey 键的列表，默认键 None；缺字段填 restval，默认 None。
PY-DictWriter|Python 3.12 CSV 字典写入额外键|DictWriter 必需 fieldnames 与 extrasaction|Python csv.DictWriter 写入多余键会怎样？|CSV 字典写入时对象多出未在 fieldnames 列出的键，默认报什么，如何忽略？|默认 extrasaction='raise'，抛 ValueError；extrasaction='ignore' 忽略额外键。
PY-mean|Python 3.12 算术平均数|mean 空数据与算术平均语义|Python statistics.mean 空输入会怎样？|统计模块求算术平均时传入空数据会返回零吗？|不会返回零；抛 StatisticsError。
PY-median|Python 3.12 中位数偶数项规则|median 偶数项与空数据|Python statistics.median 偶数个数如何取中位数？|统计模块遇到偶数项数据，中位数一定是样本中已有的一项吗？|取中间两项的平均值；不一定是原样本中的值。
PY-stdev|Python 3.12 样本标准差|stdev 与样本方差的关系|Python statistics.stdev 计算哪种标准差？|只拿到一组抽样数据，统计函数 stdev 对应样本还是总体，它与方差有什么关系？|样本标准差，是样本方差平方根。
PY-pstdev|Python 3.12 总体标准差|pstdev 与总体方差的关系|Python statistics.pstdev 计算哪种标准差？|手上是整个总体的数据，统计函数 pstdev 的结果与哪一种方差对应？|总体标准差，是总体方差平方根。
ML-StandardScaler|scikit-learn 标准化与稀疏矩阵|StandardScaler 中心化、缩放和稀疏约束|StandardScaler 能对稀疏矩阵做中心化吗？|机器学习输入是 CSR 稀疏矩阵，标准化时均值中心化为什么报错，该把哪个参数设为假？|中心化会破坏稀疏性并耗费大量内存；with_mean=False，默认中心化在稀疏输入上抛异常。
ML-MinMaxScaler|scikit-learn 最小最大缩放与新值|训练集范围、新样本越界和 clip|MinMaxScaler 会让所有新数据都在 0 到 1 吗？|用训练集极值做缩放后，新样本超出原范围会越界吗，哪个选项可截断？|默认新数据可能超出范围；clip=True 将变换值裁到 feature_range；默认范围 [0,1]。
ML-RobustScaler|scikit-learn 稳健缩放|RobustScaler 中位数与分位距|RobustScaler 默认中心和尺度是什么？|有离群值的特征做稳健缩放时，默认减去均值还是中位数，除以什么范围？|减中位数；按第 25 到第 75 百分位的四分位距缩放。
ML-OneHotEncoder|scikit-learn 独热编码未知类别|OneHotEncoder handle_unknown 行为|OneHotEncoder 的 handle_unknown='ignore' 怎样编码新类别？|测试集来了训练时从未出现的类别，独热编码设为 ignore 后对应列组是什么值，逆变换是什么？|该特征对应独热列全零；逆变换未知类别为 None。
ML-OrdinalEncoder|scikit-learn 序数编码未知类别|OrdinalEncoder unknown_value 与 use_encoded_value|OrdinalEncoder 如何指定未知类别编码？|测试集新类别想统一记为 -1，序数编码器该如何设置，值能与已有类别编码重合吗？|handle_unknown='use_encoded_value'，unknown_value=-1；unknown_value 必须不同于训练类别编码。
ML-SimpleImputer|scikit-learn 简单缺失值填补|SimpleImputer 四种基础策略及数据类型|SimpleImputer 的 mean 与 most_frequent 支持哪些数据？|缺失填补里均值策略能直接处理字符串列吗，最频繁值策略能用于哪些类型？|mean 仅数值；most_frequent 可用于字符串或数值。
ML-KNNImputer|scikit-learn 近邻缺失值填补|KNNImputer 距离度量和邻居数默认值|KNNImputer 默认 metric 和 n_neighbors 是什么？|用近邻填补缺失数据，默认找几个邻居，距离计算采用哪种缺失值友好度量？|默认 5 个邻居；metric='nan_euclidean'。
ML-train_test_split|scikit-learn 训练测试拆分约束|train_test_split 默认比例、shuffle 与 stratify|train_test_split 在 shuffle=False 时可 stratify 吗？|训练测试划分要保留原始顺序，还能同时要求分层吗？两种比例都不填时测试集占多少？|shuffle=False 时 stratify 必须 None；test_size/train_size 都未指定时 test_size=0.25。
ML-KFold|scikit-learn KFold 折数和打乱|KFold 默认折数、shuffle、random_state|KFold 默认会打乱数据吗？|普通 K 折交叉验证不传参数时分几折、是否打乱顺序，random_state 何时生效？|默认 5 折；shuffle=False；random_state 在 shuffle=True 时影响打乱。
ML-StratifiedKFold|scikit-learn 分层交叉验证|StratifiedKFold 类别比例与默认行为|StratifiedKFold 和普通 KFold 的主要区别？|分类样本类别不均衡，希望每折尽量保持各类比例，使用哪个划分器，默认几折？|StratifiedKFold；各折保持/近似保持类别比例；默认 5 折。
'''

NEGATIVES = [
('材料仿真','MAT-units','LAMMPS metal 单位制能否告诉我 304 不锈钢在 600 摄氏度时的屈服强度数值？'),
('材料仿真','MAT-timestep','我的铝合金势函数最稳定的时间步具体是多少？请根据知识库给出已经验证的值。'),
('材料仿真','MAT-fix_nh','对我的这块镍试样，NPT 控压的最佳阻尼参数经实验验证是多少？'),
('材料仿真','MAT-compute_stress_atom','知识库是否给了铜原子在所有温度下通用的精确原子体积数值？是多少？'),
('PLC自动化','PLC-TON','我们 PLC 的扫描周期精确是多少毫秒？'),
('PLC自动化','PLC-CTU','这台现场计数器断电后是否一定保留 CV？给出本设备保留配置。'),
('PLC自动化','PLC-RS','使用这个 RS 功能块就可以确认我们的急停回路已经达到 SIL3 吗？'),
('PLC自动化','PLC-TP','我们车间某台 PLC 购买许可证需要多少钱？'),
('数据库','DB-DDL-CONSTRAINTS-PRIMARY-KEYS','我们的生产 orders 表当前共有多少行、主键列实际叫什么？'),
('数据库','DB-indexes-partial','给我这台生产数据库当前所有部分索引的名称和磁盘占用。'),
('数据库','DB-XACT-SERIALIZABLE','请根据知识库给出我们生产库上周序列化失败的实际次数。'),
('数据库','DB-DDL-CONSTRAINTS-FK','我们线上 customers 外键目前配置的是 CASCADE 还是 RESTRICT？'),
('数据处理','PY-reader','昨天上传的 sales.csv 一共有多少条缺失数据？'),
('数据处理','PY-mean','请给出我们的 A 生产线今天测得的平均良率。'),
('数据处理','PY-dump','我们项目实际存储 JSON 文件的路径是哪一个？'),
('数据处理','PY-stdev','我们昨天那批试件的实际样本标准差是多少？'),
('统计建模','ML-KNNImputer','使用 KNNImputer 后我们自己的测试集准确率提高了几个百分点？'),
('统计建模','ML-StandardScaler','我们训练集第三个特征的均值和标准差实测是多少？'),
('统计建模','ML-KFold','我们上次训练五折验证的每折分数分别是多少？'),
('统计建模','ML-MinMaxScaler','给出公司传感器训练集中温度特征实际最小值和最大值。'),
]

def freeze():
    source_map={x['key']:x for x in json.loads((OUT/'source-manifest.json').read_text('utf-8'))}
    cases=[]
    per_domain={}
    for line in ROWS.strip().splitlines():
        key,title,scope,q1,q2,facts=line.split('|')
        source=source_map[key]
        domain=source['domain']
        number=per_domain.get(domain,0)
        per_domain[domain]=number+1
        case={'id':key,'domain':domain,'title':title,'scope':scope,'questions':[q1,q2],'reference_facts':facts,
              'split':'dev' if number<4 else 'heldout','source':source}
        cases.append(case)
    value={'version':1,'cards':cases,'negative_questions':[{'id':f'NEG-{i+1:02d}','domain':d,'near_card':c,'question':q,'answerable':False} for i,(d,c,q) in enumerate(NEGATIVES)],
           'acceptance':{'recall_at_3_min':0.90,'qa_correct_min':0.85,'negative_abstention_min':0.95},
           'protocol':'20 dev cards / 30 heldout cards, 2 retrieval queries per card; QA on paraphrase plus 20 unanswerable queries. No gold answers in generation/index.'}
    target=OUT/'cases.frozen.json'
    text=json.dumps(value,ensure_ascii=False,indent=2)
    if target.exists() and target.read_text('utf-8')!=text:
        raise RuntimeError('Frozen benchmark already exists; do not overwrite gold after seeing results')
    target.write_text(text,encoding='utf-8')
    (OUT/'cases.sha256').write_text(hashlib.sha256(text.encode()).hexdigest(),encoding='utf-8')
    print(f'Frozen {len(cases)} cards, {len(cases)*2} retrieval queries, {len(NEGATIVES)} unanswerable queries')

if __name__=='__main__':
    freeze()
