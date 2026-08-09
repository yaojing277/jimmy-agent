// ═══════════════════════════════════════════════════
// LINQ 練習題 — 請在每題的 TODO 區塊填入你的答案
// 完成後按 F5 執行，看輸出是否符合預期結果
// ═══════════════════════════════════════════════════

public static class Practice
{
    public static void Run()
    {
var products = new List<Product>
{
    new("可樂",   "飲料", 30,  120),
    new("礦泉水", "飲料", 15,   80),
    new("洋芋片", "零食", 45,  200),
    new("口香糖", "零食", 20,  350),
    new("雞排",   "熟食", 75,   60),
    new("珍奶",   "飲料", 65,   90),
    new("泡麵",   "零食", 38,  150),
    new("壽司",   "熟食", 110,  40),
};

// ───────────────────────────────────────────────────
// 題目 1：篩選 + 排序
//
// 找出「飲料」類別中，單價 >= 20 元的商品，
// 依單價由低到高排序，印出「名稱 - 單價」。
//
// 預期輸出：
//   可樂 - 30
//   珍奶 - 65
// ───────────────────────────────────────────────────
Console.WriteLine("【題目 1】飲料且單價 >= 20，由低到高排序");

// TODO：填入你的 LINQ 查詢
IEnumerable<Product> answer1 = products.Where(e => e.Category=="飲料" && e.Price >=20). OrderBy(e => e.Price);// ← 改這行

foreach (var p in answer1)
    Console.WriteLine($"  {p.Name} - {p.Price}");

Console.WriteLine();

// ───────────────────────────────────────────────────
// 題目 2：GroupBy + 聚合
//
// 依類別分組，計算每個類別的「總庫存量」，
// 依總庫存量由多到少排序印出。
//
// 預期輸出：
//   零食    700
//   飲料    290
//   熟食    100
// ───────────────────────────────────────────────────
Console.WriteLine("【題目 2】各類別總庫存量（由多到少）");

// TODO：填入你的 LINQ 查詢
var answer2 = products
    .GroupBy(p => p.Category)
    .Select(g => new { Category = g.Key, TotalStock = g.Sum(g => g.Stock) }) // ← 修改這行
    .OrderByDescending(x => x.TotalStock);                           // ← 修改這行

foreach (var x in answer2)
    Console.WriteLine($"  {x.Category,-6} {x.TotalStock}");

Console.WriteLine();

// ───────────────────────────────────────────────────
// 題目 3：綜合鏈式查詢
//
// 找出單價 * 庫存量（總值）前 3 名的商品，
// 依總值由高到低，印出「名稱 | 總值」。
//
// 提示：總值 = Price * Stock
//
// 預期輸出：
//   口香糖  | 7,000
//   洋芋片  | 9,000
//   泡麵    | 5,700
//   （順序依總值排列，自己算看看）
// ───────────────────────────────────────────────────
Console.WriteLine("【題目 3】總值前 3 名商品");

// TODO：填入你的 LINQ 查詢
var answer3 = products.OrderByDescending(h => h.Price*h.Stock).Take(3); // ← 改這行

foreach (var p in answer3)
    Console.WriteLine($"  {p.Name,-6} | {(p.Price * p.Stock):N0}");

Console.WriteLine();
Console.WriteLine("完成！對照預期輸出，看看答對幾題 💪");
    } // end Run()
} // end class Practice

record Product(string Name, string Category, decimal Price, int Stock);
