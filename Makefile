.PHONY: up down seed eval rules-test deploy-api deploy-intake deploy-face deploy-safety deploy-video-prep deploy-render deploy-publisher

up:
	./deploy/up.sh

down:
	./deploy/scale-down.sh

seed:
	python backend/seed.py --event demo

eval:
	python eval/run_eval.py

rules-test:
	firebase emulators:exec --only firestore "python rules-tests/run_matrix.py"

deploy-api:
	gcloud run deploy api --source backend --region us-central1

deploy-intake:
	gcloud run deploy intake --source backend --region us-central1

deploy-face:
	gcloud run deploy worker-face --source backend/workers/face --region us-central1

deploy-safety:
	gcloud run deploy worker-safety --source backend/workers/safety --region us-central1

deploy-video-prep:
	gcloud run deploy worker-video-prep --source backend/workers/video_prep --region us-central1

deploy-render:
	gcloud run jobs deploy render --source backend/render --region us-central1

deploy-publisher:
	gcloud run deploy publisher --source backend/publisher --region us-central1
