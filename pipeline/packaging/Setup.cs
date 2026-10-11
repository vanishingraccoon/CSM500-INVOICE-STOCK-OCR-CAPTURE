using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

[assembly: AssemblyTitle("Eastgate Invoice Server Setup")]
[assembly: AssemblyVersion("1.0.6.0")]

class Setup : Form
{
    readonly Button install = new Button();
    readonly TextBox details = new TextBox();
    readonly Label status = new Label();
    bool installing;
    bool finished;
    bool restartRequired;
    string setupLog;
    readonly object logLock = new object();

    static void Extract(string destination)
    {
        Directory.CreateDirectory(destination);
        foreach (string name in new string[] { "install.ps1", "payload.zip" })
        {
            using (Stream source = Assembly.GetExecutingAssembly().GetManifestResourceStream(name))
            using (FileStream output = File.Create(Path.Combine(destination, name)))
                source.CopyTo(output);
        }
    }

    Setup()
    {
        Text = "Eastgate Invoice Server Setup (1.0.6)";
        ClientSize = new Size(620, 400);
        Font = new Font("Segoe UI", 10);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        Label title = new Label();
        title.Text = "Eastgate Invoice Server";
        title.Font = new Font("Segoe UI", 17);
        title.SetBounds(20, 20, 580, 40);
        Controls.Add(title);
        status.Text = "Install the laptop application and desktop shortcut.\nKeep internet connected for installation.";
        status.SetBounds(20, 70, 580, 60);
        Controls.Add(status);
        details.Multiline = true;
        details.ReadOnly = true;
        details.ScrollBars = ScrollBars.Vertical;
        details.SetBounds(20, 140, 580, 190);
        Controls.Add(details);
        install.Text = "Install";
        install.SetBounds(470, 345, 130, 35);
        install.Click += delegate { if (finished) Close(); else BeginInstall(); };
        Controls.Add(install);
        FormClosing += delegate(object sender, FormClosingEventArgs e)
        {
            if (installing)
            {
                e.Cancel = true;
                MessageBox.Show(this, "Wait for installation to finish before closing this window.");
            }
        };
    }

    void AddLine(string line)
    {
        if (line != null && !IsDisposed)
        {
            lock (logLock)
            {
                if (setupLog != null)
                {
                    try { File.AppendAllText(setupLog, line + Environment.NewLine); }
                    catch (IOException) { }
                    catch (UnauthorizedAccessException) { }
                }
            }
            BeginInvoke((Action)delegate { details.AppendText(line + Environment.NewLine); });
        }
    }

    void BeginInstall()
    {
        installing = true;
        restartRequired = false;
        setupLog = null;
        details.Clear();
        install.Enabled = false;
        status.Text = "Installing. Keep internet connected; OCR downloads may take several minutes.";
        System.Threading.Tasks.Task.Factory.StartNew(delegate
        {
            try
            {
                string staging = Path.Combine(Path.GetTempPath(), "EastgateSetup-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(staging);
                setupLog = Path.Combine(staging, "setup.log");
                File.WriteAllText(setupLog, "Eastgate setup started" + Environment.NewLine);
                AddLine("Setup log: " + setupLog);
                Extract(staging);
                ProcessStartInfo info = new ProcessStartInfo();
                info.FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell\\v1.0\\powershell.exe");
                info.Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + Path.Combine(staging, "install.ps1") + "\"";
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.RedirectStandardOutput = true;
                info.RedirectStandardError = true;
                using (Process process = new Process())
                {
                    process.StartInfo = info;
                    process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { AddLine(e.Data); };
                    process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { AddLine(e.Data); };
                    process.Start();
                    process.BeginOutputReadLine();
                    process.BeginErrorReadLine();
                    process.WaitForExit();
                    restartRequired = process.ExitCode == 3010;
                    if (process.ExitCode != 0 && !restartRequired) throw new Exception("Installation failed. Read the details above or installation.log, then retry.");
                }
                BeginInvoke((Action)delegate
                {
                    installing = false;
                    status.Text = restartRequired
                        ? "A prerequisite installed successfully. Restart Windows, then run this setup again.\nApplication setup will continue after the restart."
                        : "Installed. Open the Eastgate Invoice Server desktop shortcut.\nThe Samsung APK is in your installation folder.";
                    install.Text = "Finish";
                    install.Enabled = true;
                    finished = true;
                });
            }
            catch (Exception error)
            {
                AddLine(error.Message);
                BeginInvoke((Action)delegate
                {
                    installing = false;
                    install.Text = "Retry";
                    install.Enabled = true;
                    status.Text = "Installation did not finish. Check the details below.\nSetup log: " + setupLog;
                });
            }
        });
    }

    [STAThread]
    static int Main(string[] args)
    {
        if (args.Length == 2 && args[0] == "--extract-only")
        {
            Extract(Path.GetFullPath(args[1]));
            return 0;
        }
        Application.EnableVisualStyles();
        Application.Run(new Setup());
        return 0;
    }
}
