"""SharePoint 同步应用程序的主入口点。"""

import asyncio
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent / "src"))

from omd_sharepoint_data.config import logging_config, sharepoint_config
from omd_sharepoint_data.scheduler import SyncScheduler


def setup_logging():
    """设置日志配置。"""
    logger.remove()
    logger.add(sys.stdout, level=logging_config.level, format=logging_config.format)
    if logging_config.file_path:
        logging_config.file_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(logging_config.file_path, level=logging_config.level, format=logging_config.format)


async def main():
    """应用程序主入口点。"""
    setup_logging()
    logger.info("Starting SharePoint sync application")

    try:
        _ = sharepoint_config.site_url
        _ = sharepoint_config.client_id
    except Exception as e:
        logger.error(f"Configuration error: {e}")
        return 1

    # Initialize scheduler
    scheduler = SyncScheduler()
    try:
        await scheduler.initialize()

        # Handle command line arguments
        if len(sys.argv) > 1:
            command = sys.argv[1]

            if command == "sync":
               logger.info("Running manual PDF sync...")
               # 支持可选的文件夹参数
               results = await scheduler.run_pdf_sync_now()
               if results:
                   logger.info(f"PDF sync completed: {results}")
               else:
                   logger.error("PDF sync failed")
                   return 1

            elif command == "cleanup":
                logger.info("Running manual cleanup...")
                deleted_count = await scheduler.run_cleanup_now()
                if deleted_count is not None:
                    logger.info(f"Cleanup completed: {deleted_count} records removed")
                else:
                    logger.error("Cleanup failed")
                    return 1

            elif command == "test":
                logger.info("Testing configuration...")
                # Basic validation is done above
                logger.info("✅ Configuration validated")

            else:
               logger.error(f"Unknown command: {command}")
               logger.info("Available commands: sync, cleanup, test")
               logger.info("  sync    - Sync PDF files from SharePoint (incremental sync)")
               logger.info("  cleanup - Remove old sync records")
               logger.info("  test    - Test configuration")
               return 1
        else:
            # Run scheduled mode
            logger.info("Starting scheduled sync mode")

            # Setup signal handlers for graceful shutdown
            def signal_handler(signum, frame):
                logger.info(f"Received signal {signum}, shutting down...")
                asyncio.create_task(scheduler.stop())

            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)

            # Start scheduler
            await scheduler.start()

            # Keep the application running
            try:
                while scheduler.scheduler.running:
                    await asyncio.sleep(1)
            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt, shutting down...")

            await scheduler.stop()

    except Exception as e:
        logger.error(f"Application error: {e}")
        return 1
    finally:
        await scheduler.stop()

    logger.info("SharePoint sync application stopped")
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
