from locust import HttpUser, task, between
import json
import time


class StreamingAIUser(HttpUser):

    wait_time = between(1, 2)

    @task
    def stream_chat(self):

        payload = {
            "message": "Explain how artificial intelligence works.",
            "temperature": 0.2,
            "max_tokens": 100
        }

        start = time.perf_counter()
        first_token_time = None
        received_tokens = 0
        completed = False

        with self.client.post(
            "/api/v1/chat/stream",
            json=payload,
            stream=True,
            catch_response=True,
            name="/api/v1/chat/stream"
        ) as response:

            try:
                if response.status_code != 200:
                    response.failure(
                        f"HTTP {response.status_code}"
                    )
                    return

                for line in response.iter_lines():

                    if not line:
                        continue

                    if isinstance(line, bytes):
                        line = line.decode("utf-8")

                    if line.startswith("data:"):
                        data = line[5:].strip()

                        if data == "[DONE]":
                            completed = True
                            break

                        try:
                            chunk = json.loads(data)
                            content = (
                                chunk.get("choices", [{}])[0]
                                .get("delta", {})
                                .get("content")
                            )

                            if content:
                                received_tokens += 1

                                if first_token_time is None:
                                    first_token_time = (
                                        time.perf_counter() - start
                                    )

                        except (ValueError, IndexError, KeyError):
                            pass

                total_time = time.perf_counter() - start

                if not completed:
                    response.failure("Stream did not complete")
                    return

                if received_tokens == 0:
                    response.failure("No generated content")
                    return

                response.success()

                if first_token_time is not None:
                    self.environment.events.request.fire(
                        request_type="STREAM",
                        name="Time To First Token",
                        response_time=first_token_time * 1000,
                        response_length=0,
                        exception=None,
                        context={}
                    )

            except Exception as exc:
                response.failure(str(exc))