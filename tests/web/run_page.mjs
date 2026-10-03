// Serve a folder, open one page in a browser, and print the page's window.testResult as JSON.
//
// Usage: node run_page.mjs FOLDER PAGE BROWSER EXECUTABLE
//
// The page sets window.testResult when it is done. Page errors and console errors are reported
// with the result. The server sends no special headers.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import puppeteer from "puppeteer-core";

const [folder, pageName, browserName, executable] = process.argv.slice(2);
const types = { ".html": "text/html", ".mjs": "text/javascript", ".js": "text/javascript",
	".wasm": "application/wasm", ".json": "application/json" };
const server = http.createServer((request, response) =>
{
	const file = path.join(folder, decodeURIComponent(new URL(request.url, "http://host").pathname));
	fs.readFile(file, (error, data) =>
	{
		if (error)
		{
			response.writeHead(404);
			response.end();
			return;
		}
		response.writeHead(200, { "Content-Type": types[path.extname(file)] ?? "application/octet-stream" });
		response.end(data);
	});
}).listen(0);

const report = { pageErrors: [], consoleErrors: [] };
const browser = await puppeteer.launch({
	executablePath: executable,
	browser: browserName === "firefox" ? "firefox" : "chrome",
	headless: true,
	args: browserName === "firefox" ? [] : ["--ignore-gpu-blocklist", "--enable-gpu"],
});
try
{
	const page = await browser.newPage();
	page.on("pageerror", (error) => report.pageErrors.push(error.message));
	page.on("console", (message) =>
	{
		if (message.type() === "error" && !message.text().includes("404"))
		{
			report.consoleErrors.push(message.text());
		}
	});
	await page.goto(`http://localhost:${server.address().port}/${pageName}`);
	await page.waitForFunction(() => window.testResult !== undefined, { timeout: 120000 });
	report.result = await page.evaluate(() => window.testResult);
}
finally
{
	await browser.close();
	server.close();
}
console.log(JSON.stringify(report));
