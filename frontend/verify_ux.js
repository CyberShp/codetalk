/* eslint-disable @typescript-eslint/no-require-imports */
const { chromium } = require('playwright');

async function run() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
  
  const taskId = "818c5ce2-8d92-4398-9719-99f003a57f57";
  const url = `http://localhost:3011/tasks/${taskId}`;
  console.log(`Opening ${url}`);
  await page.goto(url, { waitUntil: 'load', timeout: 60000 });
  console.log("Page loaded.");

  // Switch to Graph tab
  console.log("Switching to Graph tab...");
  await page.click("button:has-text('神经图谱')");
  await page.waitForTimeout(5000); 
  await page.screenshot({ path: "step1_graph_init.png" });

  // 1. Click a Process node
  console.log("Clicking a Process node...");
  const processNode = page.locator('svg >> text=Process').first();
  await processNode.click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "step2_process_selected.png" });
  console.log("Process selected.");

  // 2. Click a code node (should have cursor-pointer)
  console.log("Clicking a code node (step)...");
  const codeNode = page.locator('g.cursor-pointer').first();
  if (await codeNode.count() > 0) {
    await codeNode.click();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: "step3_code_selected.png" });
    console.log("Code node selected.");
    
    const backButton = page.locator("button:has-text('返回：')");
    if (await backButton.isVisible()) {
      const btnText = await backButton.innerText();
      console.log(`Back button is VISIBLE: ${btnText}`);
    } else {
      throw new Error("Back button is MISSING!");
    }

    // 3. Navigate to another code node to test stickiness
    console.log("Navigating to another code node to test stickiness...");
    const otherCodeNodes = await page.locator('g.cursor-pointer').all();
    if (otherCodeNodes.length > 1) {
      await otherCodeNodes[1].click();
      await page.waitForTimeout(1500);
      await page.screenshot({ path: "step4_sticky_check.png" });
      
      if (await backButton.isVisible()) {
         console.log("Back button is STILL VISIBLE (Sticky!).");
      } else {
         throw new Error("Back button GONE after navigation (Not sticky!).");
      }
      
      // 4. Test return
      console.log("Clicking back button to return...");
      await backButton.click();
      await page.waitForTimeout(1500);
      await page.screenshot({ path: "step5_returned.png" });
      console.log("Successfully returned to process view.");
    }
  } else {
    console.log("No code nodes found in the graph.");
  }
  
  await browser.close();
}

run().catch(e => { console.error(e); process.exit(1); });
