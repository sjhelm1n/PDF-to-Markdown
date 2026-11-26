FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build
FROM nginx:stable-alpine
COPY --from=build /app/dist /usr/share/nginx/html
ENV PDF2MD_MAX_UPLOAD_REQUEST_BYTES=105906176 PDF2MD_MAX_EDIT_REQUEST_BYTES=25165824
COPY nginx.conf /etc/nginx/templates/default.conf.template
EXPOSE 80
