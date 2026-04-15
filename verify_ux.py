import asyncio
from playwright.async_api import async_playwright
import os

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={'width': 1600, 'height': 1200})
        
        task_id = "818c5ce2-8d92-4398-9719-99f003a57f57"
        url = f"http://localhost:3011/tasks/{task_id}"
        print(f"Opening {url}")
        await page.goto(url)
        await page.wait_for_load_state("networkidle")

        # Switch to Graph tab
        print("Switching to Graph tab...")
        await page.click("button:has-text('神经图谱')")
        await page.wait_for_timeout(2000)
        await page.screenshot(path="step1_graph_init.png")

        # Find a Process node and click it
        # We'll try to find a text element inside the SVG
        print("Clicking a Process node...")
        # In mock or real data, we usually have some process name. 
        # Since I don't know the exact name, I'll look for any <g> that contains 'Process' label text
        process_node = page.locator("g:has(text='Process')").first
        if await process_node.count() > 0:
            await process_node.click()
            await page.wait_for_timeout(1000)
            await page.screenshot(path="step2_process_selected.png")
            print("Process selected.")
        else:
            print("No Process node found in graph!")
            await browser.close()
            return

        # Click a Step (should be a code node, e.g. 'Function' or 'Method')
        print("Clicking a code node (step)...")
        code_node = page.locator("g:has(text='Function'), g:has(text='Method'), g:has(text='Class')").first
        if await code_node.count() > 0:
            await code_node.click()
            await page.wait_for_timeout(1000)
            await page.screenshot(path="step3_code_selected.png")
            print("Code node selected. Checking sidebar for back button...")
            
            back_button = page.locator("button:has-text('返回：')")
            if await back_button.is_visible():
                print("Back button is VISIBLE.")
            else:
                print("Back button is MISSING!")
        else:
            print("No code node found in graph!")

        # Navigate to another code node to test stickiness
        print("Navigating to another code node...")
        other_code_node = page.locator("g:has(text='Function'), g:has(text='Method')").nth(1)
        if await other_code_node.count() > 0:
            await other_code_node.click()
            await page.wait_for_timeout(1000)
            await page.screenshot(path="step4_sticky_check.png")
            
            back_button = page.locator("button:has-text('返回：')")
            if await back_button.is_visible():
                print("Back button is STILL VISIBLE (Sticky!).")
            else:
                print("Back button GONE (Not sticky!).")
                
            # Test return
            print("Clicking back button...")
            await back_button.click()
            await page.wait_for_timeout(1000)
            await page.screenshot(path="step5_returned.png")
            print("Returned to process.")
        
        await browser.close()

asyncio.run(run())
