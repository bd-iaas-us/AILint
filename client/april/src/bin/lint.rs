use anyhow::{anyhow, Result};
use april::llm_client;
use april::llm_client::history;
use april::utils::git;
use april::utils::markdown;
use april::utils::spinner;
use clap::{Parser, Subcommand};
use log::{debug, warn};
use serde::Deserialize;
use std::fmt;
use std::fs::File;
use std::io::BufReader;
use std::io::Read;
use std::sync::mpsc;

#[derive(Parser)]
#[command(author, version, about, long_about = None)]
struct Cli {
    /// API URL to connect to
    #[arg(
        long,
        global = true,
        default_value = "http://localhost:8000",
        env = "API_URL"
    )]
    api_url: String,

    #[arg(long, global = true, default_value = "unknown", env = "API_KEY")]
    api_key: String,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// lint the file
    Lint {
        /// Configuration file to use
        #[clap(index = 1)]
        file_name: Option<String>,
        #[arg(long)]
        diff_mode: bool,
    },

    /// given a description, wrote a patch.
    Dev {
        /// yaml file describe the task
        #[clap(index = 1)]
        description_filename: Option<String>,
        /// read remote tasks' log
        #[arg(long, short)]
        follow: Option<String>,
    },
}

//TODO: read local rules

#[derive(Debug, Deserialize)]
struct Risk {
    which_part_of_code: String,
    reason: String,
    fix: String,
}

//TODO: should have better highlight.
impl fmt::Display for Risk {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let theme =
            bincode::deserialize_from(markdown::DARK_THEME).expect("Invalid builtin light theme");
        let mut options = markdown::RenderOptions::default();
        options.theme = Some(theme);
        options.truecolor = true;
        let mut render = markdown::MarkdownRender::init(options).unwrap();
        write!(
            f,
            "Code  :{}\nReason:{}\nFix   :{}\n",
            render.render(&self.which_part_of_code),
            render.render(&self.reason),
            render.render(&self.fix)
        )
    }
}

#[derive(Debug, Deserialize)]
struct Risks {
    risks: Vec<Risk>,    //openai could return structual data
    plain_risks: String, //other LLM will returna un-structual data
    backend: String,     //enum:openai or custom
}

#[derive(Debug, Deserialize)]
struct AILintSupportedTopics {
    topics: Vec<String>,
}

//lint
fn lint(file_name: Option<String>, diff_mode: bool, api_url: &str, api_key: &str) -> Result<()> {
    let mut project_name = String::new();
    let mut code = String::new();
    if diff_mode {
        project_name = match git::get_git_project_name() {
            Ok(p) => p,
            Err(e) => {
                println!("diff mode is only supported for git project");
                return Err(e);
            }
        };
        //if file_name is provided in diff_mode, we only lint the file itself.
        //if no file name is provided, we could lint the whole project.
        code = git::get_git_diff(&file_name)?;
    } else
    /* single file mode */
    {
        match git::get_git_project_name() {
            Ok(p) => project_name = p,
            Err(e) => {
                //if there is no git project.
            }
        };
        if file_name.is_none() {
            return Err(anyhow!("you should provide a file name to lint"));
        }
        let mut file = File::open(file_name.unwrap())?;
        file.read_to_string(&mut code)?;
    }

    let (tx, rx) = mpsc::channel();
    let handler = spinner::run_spinner("Generating", rx);

    match llm_client::lint(api_url, api_key, &project_name, &code) {
        Ok(msg) => {
            //close the fancy spinner.
            let _ = tx.send(());
            let _ = handler.join();

            //parse returned json
            match serde_json::from_str::<Risks>(&msg) {
                Ok(risks) => {
                    if risks.backend == "openai" {
                        for risk in risks.risks {
                            println!("{}", risk);
                        }
                    } else {
                        let theme = bincode::deserialize_from(markdown::DARK_THEME)
                            .expect("Invalid builtin light theme");
                        let mut options = markdown::RenderOptions::default();
                        options.theme = Some(theme);
                        options.truecolor = true;
                        let mut render = markdown::MarkdownRender::init(options).unwrap();
                        println!("{}", render.render(&risks.plain_risks));
                    }
                }
                Err(_) => {
                    println!("parse error{}", msg);
                }
            }
        }
        Err(e) => {
            let _ = tx.send(());
            let _ = handler.join();
            println!("request service error: {}", e);
        }
    }

    Ok(())
}

#[derive(Debug, Deserialize)]
struct DevTask {
    repo: String,
    description: String,
    token: Option<String>,
}

//TODO:
fn dev(
    description_filename: Option<String>,
    follow: Option<String>,
    api_url: &str,
    api_key: &str,
) -> Result<()> {
    let display_history = |chunk: &Vec<u8>| match String::from_utf8(chunk.clone()) {
        Ok(s) => print!("{}", s),
        Err(_) => {}
    };

    //follow mode
    if let Some(uuid) = follow {
        llm_client::history(api_url, api_key, &uuid, display_history);
        Ok(())
    //submit task and follow
    } else if let Some(desc_filename) = description_filename {
        let file = File::open(desc_filename).expect("Failed to open file");
        let reader = BufReader::new(file);
        let task: DevTask = serde_yaml::from_reader(reader).expect("Failed to parse YAML");

        let repo = &task.repo;
        let token = match task.token {
            Some(token) => token,
            None => "".to_string(),
        };
        let desc = task.description;
        /*
        //read local yml file. get this paramters.
        let repo = "https://github.com/bd-iaas-us/AILint.git";
        let token = "FAKE_TOKEN";
        */
        debug!("{},{},{}", repo, token, desc);
        let uuid = llm_client::dev(api_url, api_key, &repo, &token, &desc)?;
        llm_client::history(api_url, api_key, &uuid, display_history);
        Ok(())
    } else {
        println!("print usage");
        Ok(())
    }
}

fn main() -> Result<()> {
    env_logger::init();
    let cli = Cli::parse();

    match cli.command {
        Commands::Dev {
            description_filename,
            follow,
        } => dev(description_filename, follow, &cli.api_url, &cli.api_key),
        Commands::Lint {
            file_name,
            diff_mode,
        } => lint(file_name, diff_mode, &cli.api_url, &cli.api_key),
    }
}
