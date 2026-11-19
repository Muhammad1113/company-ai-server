from locust import HttpUser, task, constant


class AIUser(HttpUser):

    wait_time = constant(1)

    @task
    def chat(self):

        self.client.post(
            "/api/v1/chat/completions",
            json={
                "message": "Explain artificial intelligence briefly.",
                "temperature": 0.2,
                "max_tokens": 100
            }
        )