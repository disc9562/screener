module.exports = {
  apps : [{
    name   : "crypto-screener",
    script : "main.py",
    interpreter: "python3",
    args: "--fetch-now",
    watch: false,
    max_memory_restart: '1G',
    env_production: {
       NODE_ENV: "production"
    }
  }]
}