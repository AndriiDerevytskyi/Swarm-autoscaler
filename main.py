import os

if __name__ == "__main__":
    role = os.getenv("AUTOSCALER_ROLE", "manager")
    if role == "agent":
        from core.agent import main
    else:
        from core.engine import main
    main()
