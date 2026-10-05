from sqlalchemy import func, select

from app.core import Core
from app.db import Customer, IdSlotStat, PaymentTemplate, Pattern
from app.excel import ExcelTransaction
from app.id_slots import rebuild, slot_kind
from app.knowledge import import_confirmed
from app.normalize import normalize_text
from conftest import workbook


def learn(core, factory, raw, cid, name='Khách', row=1, date='2026-07-02', payer='VCB'):
    with factory.begin() as session:
        return core.learn(session, ExcelTransaction(row, raw, date=date, payer=payer), cid, name)


def vcb(cid, ref, name='Nguyen Van A'):
    # VCB/MB layout: no "KH" label; the IDKH sits right after "HUE WATER SUPPLY JSC.".
    return f'MB.5051-{ref % 99999:05d}-20260703-194338-06800.20260703.HUE WATER SUPPLY JSC.{cid}.{1005368166 + ref}.06/2026.{name}..1.'


def test_space_separated_and_glued_customer_id_lists():
    assert normalize_text('Frit Hue TT tien nuoc ID: 035285 035447 046165 theo HD so: 932019').customer_ids == ['035285', '035447', '046165']
    assert normalize_text('TT TIEN NUOC KY 06.2026 MA KH273426 282974 Bank Charge').customer_ids == ['273426', '282974']
    assert normalize_text('futabuslines. cn hue. mkh. 309876. 279225. t72026').customer_ids == ['279225', '309876']
    # A following date, amount or long reference never joins the list.
    assert normalize_text('MA KH 159365-020097040508041729132026KD92005277').customer_ids == ['159365']
    assert normalize_text('Ma KH 279021 7/2026 so tien 1500000').customer_ids == ['279021']
    assert normalize_text('Ma KH: 062376KBNNTTSP_KBA260810605423.Tram Y te').customer_ids == ['062376']


def test_confirmed_label_is_trusted_even_when_text_names_another_id(db):
    _, factory = db
    core = Core()
    raw = 'B/O CT TNHH CAO PHONG MKH 319416 TT Nuoc sinh hoat thang 6/2026 Kho Hue HD 00859237'
    assert learn(core, factory, raw, '008592', 'Cao Phong Kho Hue')
    with factory() as session:
        pattern = session.scalar(select(Pattern))
        assert pattern.customer_id == '008592'
        # The other written ID is not assigned to this customer.
        assert core.classify(session, raw)['customer_id'] != '008592'


def test_learned_id_position_identifies_known_and_suggests_unseen_customers(db):
    _, factory = db
    core = Core()
    for index in range(90):
        learn(core, factory, vcb(f'{100000 + index:06d}', index), f'{100000 + index:06d}', f'Khách {index}', row=index)
    with factory.begin() as session:
        stats = rebuild(session)
        assert stats['id_slots'] >= 1
        context = session.scalar(select(IdSlotStat).order_by(IdSlotStat.hits.desc()))
        assert slot_kind(context.hits, context.total) == 'id'
    with factory() as session:
        known = core.classify(session, vcb('100007', 5000, 'Tran Thi B'))
        assert known['decision'] == 'auto_accept' and known['customer_id'] == '100007'
        assert known['evidence']['reason'] == 'Explicit customer ID in a learned position plus matching payment purpose'
        unseen = core.classify(session, vcb('287654', 5001))
        assert unseen['decision'] == 'manual_check' and unseen['score'] == 0
        assert unseen['suggested_customer_id'] == '287654'
        assert unseen['normalization']['customer_ids'] == ['287654']


def test_payment_for_several_known_customers_proposes_allocation(db):
    _, factory = db
    core = Core()
    learn(core, factory, 'TT tien nuoc KH:056994', '056994', 'Cang Thuan An A')
    learn(core, factory, 'TT tien nuoc KH:057105', '057105', 'Cang Thuan An B', row=2)
    with factory() as session:
        result = core.classify(session, 'cty cp cang thuan an ck thanh toan tien nuoc thang 07 cho ma kh 056994 va ma kh 057105')
        assert result['decision'] == 'manual_check' and result['customer_id'] is None
        assert result['evidence']['allocation_customer_ids'] == ['056994', '057105']
        assert result['evidence']['allocation_complete']
        partial = core.classify(session, 'tien nuoc ma kh 056994, 999111')
        assert partial['evidence']['unknown_allocation_ids'] == ['999111']
        assert not partial['evidence']['allocation_complete']


def momo(day, ref):
    return f'VCBCSH. {ref}.MOMO TT nuoc hue tu {day:02d}/07/2026 den {day:02d}/07/2026. CT tu 0421000497559 CONG TY CP DICH VU DI DONG TRUC TUYEN'


def test_wallet_batch_settlement_is_stored_compactly_and_reported_as_batch(tmp_path):
    from app.db import Base, make_engine, sessions
    engine = make_engine('sqlite:///' + str(tmp_path / 'k.sqlite'))
    Base.metadata.create_all(engine)
    rows = [(4, {'C': 'IDKH', 'G': 'EBL', 'H': 'Ngân hàng'})]
    rows += [(5 + i, {'A': 46204, 'C': f'{200000 + i:06d}', 'D': f'Khach {i}', 'E': 10, 'G': momo(1, 1112607017322552), 'H': 'MOMO'})
             for i in range(12)]
    rows += [(20, {'A': 46204, 'C': '001545', 'D': 'Em', 'E': 10, 'G': 'TT KH 001545 tien nuoc', 'H': 'BIDV'})]
    # An organisation paying many meters in one transfer is NOT a collection service: full learning.
    rows += [(30 + i, {'A': 46204, 'C': f'{300000 + i:06d}', 'D': f'Truong {i}', 'E': 10,
                       'G': 'REM 9901CI260701000088653 B/O CONG AN TP HUE DTLS-REF/202607011010011003821901203009 TIEN NUOC', 'H': 'BIDV'})
             for i in range(11)]
    core = Core()
    summary = import_confirmed(engine, core, workbook(tmp_path / 'july.xlsx', rows))
    assert summary['counts']['batch_settlement_rows'] == 12
    factory = sessions(engine)
    with factory() as session:
        assert session.scalar(select(func.count(Pattern.id))) == 12  # BIDV customer + 11 organisation links
        assert session.scalar(select(func.count(Customer.id))) == 24
        template = session.scalar(select(PaymentTemplate).where(PaymentTemplate.settlement_rows > 0))
        assert template.max_transfer_customers == 12
        assert (template.payment_mode, template.provider_kind, template.provider_name) == ('proxy', 'wallet', 'MOMO')
        result = core.classify(session, momo(3, 1112607037402356))
        assert result['decision'] == 'manual_check' and result['customer_id'] is None
        assert result['evidence']['batch_settlement']['provider_name'] == 'MOMO'
        assert result['payment_mode'] == 'proxy'
        organisation = core.classify(session, 'REM 9901CI260801000011111 B/O CONG AN TP HUE DTLS-REF/202608011010011003829999999999 TIEN NUOC')
        assert not organisation['evidence'].get('batch_settlement')
        # A settlement customer later paying with an explicit ID and water purpose is known.
        later = core.classify(session, 'TT tien nuoc KH:200003')
        assert later['decision'] == 'auto_accept' and later['customer_id'] == '200003'
        assert later['evidence']['registry_only_customer']
        # Without a water-bill purpose it is only offered, with the confirmed name.
        unclear = core.classify(session, 'chuyen khoan KH:200004')
        assert unclear['decision'] == 'manual_check' and unclear['suggested_customer_id'] == '200004'
        assert unclear['evidence']['suggested_customer_name'] == 'Khach 4'
    engine.dispose()
