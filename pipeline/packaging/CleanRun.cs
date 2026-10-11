using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyVersion("1.0.0.0")]

class CleanRun : Form
{
    readonly Button run = new Button();
    readonly Button open = new Button();
    readonly TextBox details = new TextBox();
    readonly string install = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "EastgateInvoiceServer");
    bool busy;

    CleanRun()
    {
        Text = "Clean Run - News Cafe Eastgate";
        ClientSize = new Size(610, 360);
        StartPosition = FormStartPosition.CenterScreen;
        var explanation = new Label();
        explanation.Text = "Close the Invoice Server window first.\n\nClean Run archives the previous trial and clears batches, photos, reports,\nlearned aliases and phone pairings. It keeps the installed app and Eastgate key.\nKeep the server closed until this finishes. Pair the phone again afterwards.";
        explanation.SetBounds(20, 15, 570, 105);
        details.Multiline = true;
        details.ReadOnly = true;
        details.ScrollBars = ScrollBars.Vertical;
        details.SetBounds(20, 130, 570, 155);
        run.Text = "Start clean run";
        run.SetBounds(20, 305, 150, 32);
        run.Click += delegate { StartCleanRun(); };
        open.Text = "Open Invoice Server";
        open.SetBounds(365, 305, 225, 32);
        open.Enabled = false;
        open.Click += delegate
        {
            try
            {
                string script = Path.Combine(install, "Start-Eastgate.ps1");
                if (!File.Exists(script)) throw new Exception("The server launcher is missing. Run the installer again.");
                Process.Start(new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"" + script + "\"")
                { UseShellExecute = false, CreateNoWindow = true });
                Close();
            }
            catch (Exception error) { MessageBox.Show(error.Message, "Cannot open server"); }
        };
        FormClosing += delegate(object sender, FormClosingEventArgs args) { args.Cancel = busy; };
        Controls.AddRange(new Control[] { explanation, details, run, open });
    }

    static void Extract(string directory)
    {
        Directory.CreateDirectory(directory);
        using (Stream source = Assembly.GetExecutingAssembly().GetManifestResourceStream("clean-run.ps1"))
        using (Stream target = File.Create(Path.Combine(directory, "clean-run.ps1"))) source.CopyTo(target);
    }

    void StartCleanRun()
    {
        busy = true;
        run.Enabled = false;
        details.Text = "Preparing a fresh trial...";
        System.Threading.Tasks.Task.Factory.StartNew(delegate
        {
            bool success = false;
            string result;
            try
            {
                string directory = Path.Combine(Path.GetTempPath(), "EastgateCleanRun-" + Guid.NewGuid().ToString("N"));
                Extract(directory);
                var info = new ProcessStartInfo("powershell.exe", "-NoProfile -ExecutionPolicy Bypass -File \"" + Path.Combine(directory, "clean-run.ps1") + "\"");
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.RedirectStandardOutput = true;
                info.RedirectStandardError = true;
                using (var process = Process.Start(info))
                {
                    var error = process.StandardError.ReadToEndAsync();
                    result = process.StandardOutput.ReadToEnd();
                    process.WaitForExit();
                    result += error.Result;
                    success = process.ExitCode == 0;
                }
            }
            catch (Exception error) { result = error.Message; }
            BeginInvoke((Action)delegate
            {
                details.Text = result;
                busy = false;
                run.Enabled = !success;
                open.Enabled = success;
            });
        });
    }

    [STAThread]
    static void Main(string[] args)
    {
        if (args.Length == 2 && args[0] == "--extract-only") { Extract(args[1]); return; }
        Application.EnableVisualStyles();
        Application.Run(new CleanRun());
    }
}
