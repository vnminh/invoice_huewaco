"""Optional UI smoke test using system Chrome and an isolated temporary database."""
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time

import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from app.core import Core
from app.db import Base, make_engine, sessions
from app.excel import ExcelTransaction
from app.main import create_app
from conftest import workbook


def main():
    with tempfile.TemporaryDirectory(prefix='invoice-ui-smoke-') as directory:
        directory = Path(directory)
        engine = make_engine('sqlite:///' + str(directory / 'knowledge.sqlite'))
        Base.metadata.create_all(engine)
        core = Core()
        history = 'REM Tfr Ac:8828533184 O@L_040001_212501_0_0_2143591348_207241_E5564054_TT tien nuoc ky 6/2026 E5564054_207241_HD:207241@@6/2026; thoi gian GD:01/07/2026 00:08:46'
        with sessions(engine).begin() as session:
            core.learn(session, ExcelTransaction(5, history, date='2026-07-01', source='July confirmed history.xlsx'),
                       '207241', 'Khách hàng demo')
        api = create_app(engine, core, directory/'runtime')
        with socket.socket() as bound:
            bound.bind(('127.0.0.1',0))
            port = bound.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(api, host='127.0.0.1',port=port,log_level='warning'))
        thread = threading.Thread(target=server.run,daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            assert server.started
            screenshots = ROOT / 'reports'
            screenshots.mkdir(exist_ok=True)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=shutil.which('google-chrome'),
                    headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
                page = browser.new_page(viewport={'width':1440,'height':1050})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{port}')
                page.wait_for_function("document.getElementById('customerCount').textContent === '1'")
                assert page.locator('#workspace').is_visible()
                assert not page.locator('#knowledge').is_visible()
                assert page.locator('#benchmark').count() == 0
                assert page.locator('#singlePanel').count() == 0
                page.click('#tab-knowledge')
                assert page.locator('#knowledgeForm').is_visible()
                assert not page.locator('#workspace').is_visible()
                page.locator('#tab-knowledge').press('ArrowRight')
                assert page.locator('#database').is_visible()
                page.wait_for_selector('#databaseRows [data-database-row]')
                page.locator('#tab-database').press('Home')
                assert page.locator('#workspace').is_visible()
                raw = workbook(directory/'raw.xlsx',[(12,{'B':'Số tham chiếu','I':'Mô tả'}),
                    (14,{'B':'demo-ref','C':'01/08/2026','E':100000,'D':0,'I':history}),
                    (15,{'B':'unknown-ref','C':'01/08/2026','E':100000,'D':0,'I':'TT KH 999999 tien nuoc thang 08/2026'})])
                page.set_input_files('#rawFile',str(raw))
                page.click('#batchForm button[type=submit]')
                page.wait_for_selector('#jobs a[href^="/export/"]',state='attached',timeout=15000)
                known = page.locator('#transactionRows tr').filter(has_text='Khách hàng demo')
                known.wait_for()
                assert 'Có thể xác nhận' in known.inner_text()
                known.get_by_role('button',name='Kiểm tra').click()
                assert 'July confirmed history.xlsx' in page.locator('#reviewBody').inner_text()
                assert '207241' in page.locator('#reviewBody').inner_text()
                assert page.locator('#acceptReview').is_visible()
                assert not page.locator('#numberDetails').evaluate('(element) => element.open')
                page.click('#numberDetails > summary')
                assert 'Đối chiếu số theo đúng thứ tự' in page.locator('#reviewBody').inner_text()
                assert 'Mã hợp đồng' in page.locator('#reviewBody').inner_text()
                page.click('#templateDetails > summary')
                assert 'Mẫu trong lịch sử' in page.locator('#reviewBody').inner_text()
                page.screenshot(path=str(screenshots/'match_evidence.png'))
                page.click('#acceptReview')
                page.wait_for_selector('#modalBg',state='hidden')
                page.wait_for_function("document.getElementById('fileConfirmed').textContent === '1'")
                unknown = page.locator('#transactionRows tr').filter(has_text='TT KH 999999')
                assert 'Kiểm tra thủ công' in unknown.inner_text()
                assert '0.0%' in unknown.inner_text()
                page.locator('#toast').evaluate("(element) => element.classList.add('hidden')")
                page.screenshot(path=str(screenshots/'ui_preview.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                page.screenshot(path=str(screenshots/'ui_mobile.png'),full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                assert page.locator('#transactionRows tr').first.locator('td').nth(1).is_visible()
                assert page.locator('#transactionRows tr').first.locator('td').nth(4).is_visible()
                assert page.locator('#transactionRows tr').first.locator('td').nth(4).bounding_box()['x'] < 390
                page.click('#tab-knowledge')
                confirmed = workbook(directory/'confirmed.xlsx',[(4,{'C':'IDKH','G':'LỆNH GỐC NGÂN HÀNG'}),
                    (5,{'A':46204,'C':'001111','D':'Khách mới','E':100000,'G':'TT KH 001111 tien nuoc','H':'BIDV'})])
                page.set_input_files('#knowledgeFile',str(confirmed))
                page.check('#confirmedCheck')
                page.click('#knowledgeForm button[type=submit]')
                page.wait_for_function("document.getElementById('customerCount').textContent === '2'",timeout=15000)
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                assert not errors, errors
                browser.close()
            print('Browser smoke passed: admin tabs, classification, history evidence, immediate confirmation learning, import, unknown 0%, CSV, desktop/mobile layout.')
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            api.state.executor.shutdown(wait=True)
            engine.dispose()


if __name__ == '__main__':
    main()
