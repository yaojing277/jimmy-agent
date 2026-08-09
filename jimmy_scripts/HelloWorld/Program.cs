Console.WriteLine("=== .NET 8 環境測試 ===");
Console.WriteLine($"執行時間：{DateTime.Now:yyyy-MM-dd HH:mm:ss}");
Console.WriteLine($".NET 版本：{Environment.Version}");
Console.WriteLine($"作業系統：{Environment.OSVersion}");
Console.WriteLine();

// ─────────────────────────────────────────
// 範例資料
// ─────────────────────────────────────────
int[] numbers = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

var employees = new List<Employee>
{
    new("Alice",   "IT",      85000, 3),
    new("Bob",     "HR",      60000, 7),
    new("Charlie", "IT",      92000, 5),
    new("Diana",   "Finance", 78000, 2),
    new("Eve",     "HR",      65000, 4),
    new("Frank",   "IT",      70000, 1),
    new("Grace",   "Finance", 95000, 9),
};

// ─────────────────────────────────────────
// 1. Where — 篩選
// ─────────────────────────────────────────
Console.WriteLine("【1】Where 篩選：薪水 > 75000");
var highSalary = employees.Where(e => e.Salary > 75000);
foreach (var e in highSalary)
    Console.WriteLine($"  {e.Name} ({e.Department}) - {e.Salary:N0}");

Console.WriteLine();

// ─────────────────────────────────────────
// 2. Select — 投影 (轉換)
// ─────────────────────────────────────────
Console.WriteLine("【2】Select 投影：取出姓名清單");
var names = employees.Select(e => e.Name);
Console.WriteLine("  " + string.Join(", ", names));

Console.WriteLine();

// ─────────────────────────────────────────
// 3. OrderBy / OrderByDescending — 排序
// ─────────────────────────────────────────
Console.WriteLine("【3】OrderByDescending 排序：薪水由高到低");
var sorted = employees.OrderByDescending(e => e.Salary);
foreach (var e in sorted)
    Console.WriteLine($"  {e.Name,-10} {e.Salary,8:N0}");

Console.WriteLine();

// ─────────────────────────────────────────
// 4. GroupBy — 分組
// ─────────────────────────────────────────
Console.WriteLine("【4】GroupBy 分組：依部門統計人數與平均薪水");
var groups = employees
    .GroupBy(e => e.Department)
    .Select(g => new
    {
        Department = g.Key,
        Count      = g.Count(),
        AvgSalary  = g.Average(e => e.Salary),
    })
    .OrderBy(g => g.Department);

foreach (var g in groups)
    Console.WriteLine($"  {g.Department,-10} 人數:{g.Count}  平均薪:{g.AvgSalary:N0}");

Console.WriteLine();

// ─────────────────────────────────────────
// 5. First / FirstOrDefault — 取第一筆
// ─────────────────────────────────────────
Console.WriteLine("【5】FirstOrDefault：找第一位 IT 部門員工");
var firstIT = employees.FirstOrDefault(e => e.Department == "IT");
Console.WriteLine($"  {firstIT?.Name ?? "查無資料"}");

Console.WriteLine();

// ─────────────────────────────────────────
// 6. Any / All — 判斷條件
// ─────────────────────────────────────────
Console.WriteLine("【6】Any / All 條件判斷");
bool anyHR      = employees.Any(e => e.Department == "HR");
bool allPositive = employees.All(e => e.Salary > 0);
Console.WriteLine($"  有 HR 部門員工？{anyHR}");
Console.WriteLine($"  所有薪水皆 > 0？{allPositive}");

Console.WriteLine();

// ─────────────────────────────────────────
// 7. Sum / Min / Max / Average — 聚合
// ─────────────────────────────────────────
Console.WriteLine("【7】聚合函式：整體薪資統計");
Console.WriteLine($"  總薪資：{employees.Sum(e => e.Salary):N0}");
Console.WriteLine($"  最低薪：{employees.Min(e => e.Salary):N0}");
Console.WriteLine($"  最高薪：{employees.Max(e => e.Salary):N0}");
Console.WriteLine($"  平均薪：{employees.Average(e => e.Salary):N0}");

Console.WriteLine();

// ─────────────────────────────────────────
// 8. Take / Skip — 分頁
// ─────────────────────────────────────────
Console.WriteLine("【8】Take / Skip 分頁：每頁 3 筆，取第 2 頁");
int pageSize = 3, page = 2;
var paged = employees
    .OrderBy(e => e.Name)
    .Skip((page - 1) * pageSize)
    .Take(pageSize);
foreach (var e in paged)
    Console.WriteLine($"  {e.Name}");

Console.WriteLine();

// ─────────────────────────────────────────
// 9. Distinct / Count — 去重與計數
// ─────────────────────────────────────────
Console.WriteLine("【9】Distinct：有哪些部門");
var departments = employees.Select(e => e.Department).Distinct().Order();
Console.WriteLine("  " + string.Join(", ", departments));

Console.WriteLine();

// ─────────────────────────────────────────
// 10. 串接多個 LINQ (鏈式查詢)
// ─────────────────────────────────────────
Console.WriteLine("【10】鏈式查詢：IT 部門年資 >= 3 年，薪水由高到低");
var result = employees
    .Where(e => e.Department == "IT" && e.YearsOfService >= 3)
    .OrderByDescending(e => e.Salary)
    .Select(e => $"{e.Name} 薪:{e.Salary:N0} 年資:{e.YearsOfService}年");

foreach (var r in result)
    Console.WriteLine($"  {r}");

Console.WriteLine();

// ─────────────────────────────────────────
// 原始數字範例
// ─────────────────────────────────────────
var evenSum = numbers.Where(n => n % 2 == 0).Sum();
Console.WriteLine($"1~10 偶數總和：{evenSum}");

var person = new Person("Jimmy", "高雄");
Console.WriteLine($"使用者：{person.Name}，來自 {person.City}");

Console.WriteLine();
Console.WriteLine("✅ LINQ 範例執行完畢！");

Console.WriteLine();
Console.WriteLine("════════════════════════════════");
Console.WriteLine("         LINQ 練習題");
Console.WriteLine("════════════════════════════════");
Practice.Run();

// ─────────────────────────────────────────
// 資料模型
// ─────────────────────────────────────────
record Person(string Name, string City);
record Employee(string Name, string Department, decimal Salary, int YearsOfService);
