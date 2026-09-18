import puppeteer from 'puppeteer';
const BASE = 'http://tmshot-app:5000';
const sleep = ms => new Promise(r => setTimeout(r, ms));
const browser = await puppeteer.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage', '--force-color-profile=srgb'] });
const missing = [];

const PANELS = [
    [0, 'setup-welcome'],
    [1, 'setup-connection'],
    [2, 'setup-self-route'],
    [3, 'setup-monitoring'],
    [4, 'setup-crowdsec'],
    [5, 'setup-git-backup'],
    [6, 'setup-notifications'],
    [7, 'setup-password'],
];

for (const theme of ['dark', 'light']) {
    const ctx  = await browser.createBrowserContext();
    const page = await ctx.newPage();
    await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 2 });
    const js = code => page.evaluate(code);

    await page.goto(BASE + '/login', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await js(`localStorage.setItem('tm-theme', '${theme}')`);
    await page.type('#password', 'screenshot-demo-password');
    await Promise.all([
        page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 60000 }),
        page.click('form[action$="/login"] button[type="submit"]'),
    ]);
    await page.goto(BASE + '/setup', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await sleep(1500);
    if (!(await js(`!!document.getElementById('panel-7')`))) {
        throw new Error('setup wizard did not render its final panel');
    }

    const fill = async (fields) => {
        const gone = await page.evaluate((pairs) => Object.entries(pairs).filter(([id, value]) => {
            const el = document.getElementById(id);
            if (!el) return true;
            el.value = value;
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return false;
        }).map(([id]) => id), fields);
        gone.forEach(id => missing.push(`${theme}: #${id} is gone from the setup wizard`));
    };

    await fill({ s_apiurl: 'http://traefik:8080', s_domains: 'example.com, example.lan',
                 s_resolver: 'letsencrypt' });
    await js(`['dashboard','routemap','certs','logs'].forEach(t => { if (!setupTabs[t]) toggleSetupTab(t); })`);
    await js(`if (!setupGeoip) toggleSetupGeoip()`);
    await fill({ s_cs_url: 'http://crowdsec:8080', s_cs_key: 'a1b2c3d4e5f6a7b8c9d0',
                 s_cs_machine_id: 'traefik-manager', s_cs_machine_pw: 'a1b2c3d4e5f6a7b8c9d0' });
    await fill({ s_git_repo: 'https://github.com/you/traefik-configs', s_git_user: 'you',
                 s_git_token: 'ghp_a1b2c3d4e5f6a7b8c9d0' });
    await fill({ s_notify_kind: 'discord' });
    await sleep(400);
    await fill({ s_notify_url: 'https://discord.com/api/webhooks/1234567890/aBcDeFgHiJkLmNoP' });
    await fill({ s_password: 'correct-horse-battery', s_confirm: 'correct-horse-battery' });
    await js(`if (typeof checkPwMatch === 'function') checkPwMatch()`);

    for (const [step, name] of PANELS) {
        await js(`goTo(${step})`);
        await sleep(900);
        await page.screenshot({ path: `/out/${theme}/${name}.png` });
        console.log(`${theme}/${name}`);
    }
    await ctx.close();
}
await browser.close();
if (missing.length) {
    console.log('SETUP WIZARD FIELDS THAT MOVED, the panels still shot but with empty fields:');
    missing.forEach(m => console.log('  ! ' + m));
}
