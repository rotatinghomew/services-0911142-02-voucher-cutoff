from .validation import require_tables, unique, finite, references


SCHEMA = {'applications': {'application': ['str'], 'year': ['str'], 'applied_day': ['number'], 'age_days': ['number'], 'voucher_used': ['boolean'], 'visit_60d': ['null', 'number'], 'observed_days': ['number'], 'income_band': ['number'], 'center': ['str'], 'recorded_at': ['number']}, 'age_history': {'change': ['str'], 'application': ['str'], 'visible_at': ['number'], 'age_days': ['number']}, 'policies': {'year': ['str'], 'cutoff_days': ['number'], 'eligibility': ['str'], 'followup_days': ['number'], 'data_cutoff': ['number'], 'decision_vintage': ['number']}}
OPTIONS = {'year': ['2025', '2026'], 'window': ['45', '90']}


def validate_structure(data, schema):
    require_tables(data, list(schema))
    for table, fields in schema.items():
        for row in data[table]:
            if not isinstance(row, dict) or any(field not in row for field in fields):
                raise ValueError("表字段缺失：" + table)
            for field, kinds in fields.items():
                value = row[field]
                kind = "null" if value is None else ("boolean" if isinstance(value,bool) else ("number" if isinstance(value,(int,float)) else type(value).__name__))
                if kind not in kinds:
                    raise ValueError("字段类型错误：" + table + "." + field)
                if kind == "number":
                    finite(value)


def validate(data):
    validate_structure(data, SCHEMA)
    unique(data['applications'], ['application'])
    unique(data['age_history'], ['change'])
    unique(data['policies'], ['year'])
    references(data['age_history'], 'application', data['applications'], 'application')
    for row in data["applications"]:
        finite(row["age_days"], minimum=0)
        finite(row["observed_days"], minimum=0)
        if row["visit_60d"] not in (None,0,1):
            raise ValueError("入托结局只能是未观测、零或一")


def view(rows, measure, note):
    return {"summary":{"明细条数":len(rows),"有数值记录":sum(isinstance(row.get(measure),(int,float)) for row in rows)},
            "rows":rows,"series":[{"label":str(position+1),"y":row.get(measure)} for position,row in enumerate(rows)],
            "notes":[note]}


def explore(data, selection):
    for key, choices in OPTIONS.items():
        if selection.get(key) not in choices:
            raise ValueError("选择范围无效：" + key)
    policy = next(row for row in data["policies"] if row["year"] == selection["year"])
    rows = [{**row,"distance":row["age_days"]-policy["cutoff_days"],"eligible":row["age_days"] < policy["cutoff_days"]} for row in data["applications"] if row["year"] == selection["year"] and abs(row["age_days"]-policy["cutoff_days"]) <= int(selection["window"])]
    return view(rows, "distance", "当前只列申请时记录，不替代历史截面核验、断点估计或因果解释。")
